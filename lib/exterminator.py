import os, signal, select, json
from multiprocessing import Process, Pipe
from multiprocessing.connection import Listener, Client

def gdb_to_py(name, value, found=None, fullname=None, separator='.'):
    if found is None:
        found = { '0x0': 'nullptr' }
    if fullname is None:
        fullname = name
    elif separator not in [ True, False ]:
        fullname = fullname + separator + name
    t = value.type
    try:
        s = (u"%s" % value).encode('utf8')
    except gdb.error as e:
        return { "%s (%s)" % (name, t): { str(e) : "" } }

    tn = str(t)

    if t.code == gdb.TYPE_CODE_TYPEDEF:
        t = t.strip_typedefs()

    if t.code == gdb.TYPE_CODE_PTR:
        if s in found.keys():
            if t.target().code != gdb.TYPE_CODE_INT and t.target().code != gdb.TYPE_CODE_BOOL:
                return { "%s (%s)" % (name, tn): { found[s]: "" } }
        else:
            found[s] = fullname

        if str(t.target().unqualified()) == 'char':
            try:
                s = "Cannot decode object"
                for encoding in [ 'utf8', 'ascii' ]:
                    try:
                        s = '%s' % value.string(encoding)
                        break
                    except UnicodeDecodeError:
                        pass
                s = '"%s"' % s.encode("unicode-escape")
                return { "%s (%s)" % (name, tn): { s : "" } }
            except gdb.error as e:
                return { "%s (%s)" % (name, tn): { str(e) : "" } }
            except gdb.MemoryError as e:
                return { "%s (%s)" % (name, tn): { str(e) : "" } }

        t = t.target()
        fullname = fullname + '->'
        try:
            return gdb_to_py(name, value.dereference(), found, fullname, False)
        except gdb.error as e:
            return { "%s (%s)" % (name, tn): { str(e) : "" } }
    elif t.code == gdb.TYPE_CODE_REF:
        if s in found.keys():
            return { "%s (%s)" % (name, tn): { found[s]: "" } }
        found[s] = fullname
        t = t.target()

    if t.code == gdb.TYPE_CODE_STRUCT:
        contents = {}
        for field in t.fields():
            separator_to_use = '' if separator is False else '.'
            if not field.is_base_class:
                try:
                    child = value[field.name]
                    this = gdb_to_py(field.name, child, found, fullname, separator_to_use)
                except gdb.error as e:
                    this = { "%s (%s)" % (name, tn): { str(e) : "" } }
            else:
                try:
                    child = value.cast(field.type)
                    this = gdb_to_py(field.name, child, found, fullname, separator_to_use)
                except gdb.error as e:
                    this = { "%s (%s)" % (name, tn): { str(e) : "" } }
                separator_to_use = True
            contents = dict(contents, **this)
        return { "%s (%s)" % (name, tn): contents }
    else:
        return { "%s (%s)" % (name, tn): { s: "" } }

class Gdb(object):
    def __init__(self, sock, proxy):
        try:
            self.vim_tmux_pane = os.environ['VIM_TMUX_PANE']
        except KeyError:
            self.vim_tmux_pane = None
        self.sock = sock
        self.next_breakpoint = 2
        self.proxy = proxy

        self.breakpoints = { }
        self.filename = None
        self.line = None

        hello = self.sock.recv()
        assert hello['op'] == 'init', str(hello)

        if self.vim_tmux_pane:
            os.system('tmux send-keys -t %s "\x1b\x1b:GdbConnect" ENTER' % (self.vim_tmux_pane))

        gdb.execute("set pagination off")

    def attach_hooks(self):
        def on_prompt(prompt):
            try:
                self.handle_events()
            except:
                import traceback
                traceback.print_exc()
            try:
                self.goto_frame(gdb.selected_frame())
            except gdb.error:
                pass
            self.mark_breakpoints()
        gdb.prompt_hook = on_prompt

        def kill_server(e):
            self.vim(op='quit')
            self.vim(dest='proxy', op='quit')
        gdb.events.exited.connect(kill_server)

    def dettach_hooks(self):
        gdb.prompt_hook = None
        gdb.events.exited.connect(None)

    def vim(self, **kwargs):
        p = dict({'dest': 'vim'}, **kwargs)
        self.sock.send(p)
        if self.vim_tmux_pane and p['dest'] == 'vim' and p['op'] != 'quit':
            os.system('tmux send-keys -t %s "\x1b\x1b:GdbRefresh" ENTER' % (self.vim_tmux_pane))

    def handle_events(self):
        if not self.sock.poll():
            return
        sigint_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            while True:
                try:
                    c = self.sock.recv()
                except IOError:
                    print "Connection to VIM reset by peer.  Continuing as normal GDB session."
                    self.detach_hooks()
                    return
                if c['op'] == 'exec':
                    try:
                        print c['comm']
                        gdb.execute(c['comm'])
                    except gdb.error as e:
                        print str(e)
                elif c['op'] == 'go':
                    try:
                        if gdb.selected_inferior().pid == 0:
                            print 'r'
                            gdb.execute('r')
                        else:
                            print 'c'
                            gdb.execute('c')
                    except gdb.error as e:
                        print str(e)
                elif c['op'] == 'eval':
                    try:
                        value = gdb.parse_and_eval(c['expr'])

                    except gdb.error as e:
                        contents = { c['expr']: { str(e): "" } }

                    else:
                        contents = gdb_to_py(c['expr'], value)

                    if len(contents) == 1:
                        expr, value = contents.items()[0]
                        c['expr'] = expr
                        contents = value
                    self.vim(op='tree', expr=c['expr'], contents=contents)
                elif c['op'] == 'locals':
                    expr = 'locals'
                    variables = [ 'this' ]
                    variables += [ a.split(' = ', 1)[0] for a in gdb.execute("info args", to_string=True).split('\n')[:-1] ]
                    variables += [ l.split(' = ', 1)[0] for l in gdb.execute("info locals", to_string=True).split('\n')[:-1] ]
                    contents = {}
                    for var in variables:
                        try:
                            value = gdb.parse_and_eval(var)

                        except gdb.error as e:
                            if var != 'this':
                                contents = dict(contents, **{ var: { str(e): "" } })

                        else:
                            contents = dict(contents, **gdb_to_py(var, value))

                    self.vim(op='tree', expr=expr, contents=contents)
                elif c['op'] == 'disable':
                    self.disable_breakpoints(*c['loc'])
                elif c['op'] == 'toggle':
                    self.toggle_breakpoints(*c['loc'])
                elif c['op'] == 'goto':
                    self.toggle_breakpoints(*c['loc'])
                elif c['op'] == 'quit':
                    gdb.execute('quit')
                if not self.sock.poll():
                    return
        finally:
            signal.signal(signal.SIGINT, sigint_handler)

    def goto_frame(self, frame):
        filename, line = self.to_loc(frame.find_sal())
        self.goto_file(filename, line)

    def goto_file(self, filename, line):
        if not os.path.exists(filename):
            return
        if filename == self.filename and line == self.line:
            return
        self.filename = filename
        self.line = line
        self.vim(op='goto', line=line, filename=filename)

    def to_loc(self, sal):
        return sal.symtab.filename, int(sal.line)

    def get_locations(self, breakpoint):
        unparsed, locs = gdb.decode_line(breakpoint.location)
        if locs:
            return [ self.to_loc(loc) for loc in locs ]

    def mark_breakpoints(self):
        if gdb.breakpoints() is None:
            self.vim(op='unplace', num='*')
            return
        new_breakpoints = set([])
        for breakpoint in gdb.breakpoints():
            for filename, line in self.get_locations(breakpoint):
                if breakpoint.enabled:
                    new_breakpoints.add((filename, line))

        old_breakpoints = set(self.breakpoints.keys())
        remove = old_breakpoints - new_breakpoints
        add = new_breakpoints - old_breakpoints

        for filename, line in remove:
            self.vim(op='unplace', num=self.breakpoints[(filename, line)])
            del self.breakpoints[(filename, line)]

        for filename, line in add:
            print filename, line
            self.breakpoints[(filename, line)] = self.next_breakpoint
            self.vim(op='place', num=self.next_breakpoint, name='breakpoint', line=line, filename=filename)
            self.next_breakpoint += 1

        if self.filename is not None and self.line is not None:
            sign = 'pc_and_breakpoint' if (self.filename, self.line) in self.breakpoints.keys() else 'just_pc'
            self.vim(op='unplace', num=1)
            self.vim(op='place', num=1, name=sign, line=self.line, filename=self.filename)

    def disable_breakpoints(self, filename, line):
        for breakpoint in gdb.breakpoints():
            for old_filename, old_line in self.get_locations(breakpoint):
                if (old_filename, old_line) == (filename, line):
                    breakpoint.delete()

    def toggle_breakpoints(self, filename, line):
        found = False
        if gdb.breakpoints() is not None:
            for breakpoint in gdb.breakpoints():
                for old_filename, old_line in self.get_locations(breakpoint):
                    if (old_filename, old_line) == (filename, line):
                        breakpoint.delete()
                        found = True
        if not found:
            gdb.execute("break %s:%d" % (filename, line))

    def continue_until(self, filename, line):
        pass # Unimplemented

class RemoteGdb(object):
    def __init__(self, vim, host, port):
        self.vim = vim
        self.sock = Client((host, port))

    def send_command(self, **kwargs):
        try:
            self.sock.send(dict({'dest': 'gdb'}, **kwargs))
        except IOError:
            print "Broken pipe encountered sending to the proxy.  Terminating Exterminator."
            self.quit()

    def handle_events(self):
        if not self.sock.poll():
            return
        while True:
            try:
                c = self.sock.recv()
            except (IOError, EOFError):
                print "Lost connection to GDB"
                self.quit()
                return
            if c['op'] == 'goto':
                window = self.find_window('navigation')
                if window is None:
                    self.claim_window('navigation')
                if os.path.abspath(self.vim.current.buffer.name) != c['filename']:
                    self.vim.command("e %(filename)s" % c)
                self.vim.command("%(line)s" % c)
                self.vim.command("let &ft=&ft")
                self.vim.command("%(line)skP" % c)
            elif c['op'] == 'disp':
                winnr = int(self.vim.eval("winnr()"))
                window = self.find_window('display', 'bot 15new')
                self.vim.command("setlocal buftype=nowrite bufhidden=wipe modifiable nobuflisted noswapfile nowrap nonumber")
                contents = [ c['expr'], c['contents'] ]
                self.vim.current.window.buffer[:] = contents
                self.vim.command("setlocal nomodifiable")
                self.vim.command("%swincmd w" % winnr)
            elif c['op'] == 'tree':
                contents = json.dumps(c['contents']).replace("'", "''")
                expr = c['expr'].replace("'", "''")
                self.vim.command("call NERDTreeFromJSON('%s', '%s')" % (expr, contents))
            elif c['op'] == 'place':
                self.vim.command("badd %(filename)s" % c)
                self.vim.command("sign place %(num)s name=%(name)s line=%(line)s file=%(filename)s" % c)
            elif c['op'] == 'unplace':
                self.vim.command("sign unplace %(num)s" % c)
            elif c['op'] == 'quit':
                self.quit()
                return
            if not self.sock.poll():
                return

    def quit(self):
        self.vim.command("sign unplace *")
        winnr = int(self.vim.eval("winnr()"))
        window = self.find_window('display')
        if window is not None:
            self.vim.command("q")
        self.vim.command("%swincmd w" % winnr)
        self.vim.gdb = None
        try:
            self.send_command(dest='proxy', op='quit')
        except:
            pass

    def send_trap(self):
        self.send_command(dest='proxy', op='trap')

    def send_quit(self):
        self.send_command(op='quit')

    def send_continue(self):
        self.send_command(op='go')
        self.send_trap()
        self.handle_events()

    def send_next(self):
        self.send_command(op='exec', comm='next')
        self.send_trap()
        self.handle_events()

    def send_step(self):
        self.send_command(op='exec', comm='step')
        self.send_trap()
        self.handle_events()

    def send_break(self, filename, line):
        self.send_command(op='exec', comm='break %s:%d' % (filename, line))
        self.send_trap()
        self.handle_events()

    def disable_break(self, filename, line):
        self.send_command(op='disable', loc=(filename, line))
        self.send_trap()
        self.handle_events()

    def toggle_break(self, filename, line):
        self.send_command(op='toggle', loc=(filename, line))
        self.send_trap()
        self.handle_events()

    def continue_until(self, filename, line):
        self.send_command(op='goto', loc=(filename, line))
        self.send_trap()
        self.handle_events()

    def eval_expr(self, expr):
        self.send_command(op='eval', expr=str(expr))
        self.send_trap()
        self.handle_events()

    def get_locals(self):
        self.send_command(op='locals')
        self.send_trap()
        self.handle_events()

    def claim_window(self, window_name):
        self.vim.command('let b:mandrews_output_window = "%s"' % window_name)

    def find_window(self, window_name, new_command=None):
        winnr = int(self.vim.eval("winnr()"))
        while True:
            if int(self.vim.eval("exists('b:mandrews_output_window')")) > 0:
                if str(self.vim.eval("b:mandrews_output_window")) == window_name:
                    break
            self.vim.command("wincmd w")
            if winnr == int(self.vim.eval("winnr()")):
                if new_command is not None:
                    self.vim.command(new_command)
                    self.claim_window(window_name)
                    break
                return
        return self.vim.current.window

def HandleProxyRequest(c):
    if c['op'] == 'trap':
        os.kill(os.getppid(), signal.SIGINT)
    elif c['op'] == 'quit':
        exit(0)
    else:
        print "Proxy packet with unknown op: " + str(c)

def ProxyConnection(vim_conn, gdb_conn):
    while True:
        try:
            for ready in select.select([vim_conn, gdb_conn], [], [])[0]:
                c = ready.recv()
                if c['dest'] == 'proxy':
                    HandleProxyRequest(c)
                elif c['dest'] == 'vim':
                    vim_conn.send(c)
                elif c['dest'] == 'gdb':
                    gdb_conn.send(c)
                else:
                    print "Packet with unknown dest: " + str(c)
        except EOFError:
            print "EOF encountered in the proxy.  Terminating GDB."
            try:
                vim_conn.send({'op': 'quit', 'dest': 'vim'})
            except IOError:
                pass
            os.kill(os.getppid(), signal.SIGTERM)
        except IOError:
            print "Broken pipe encountered in the proxy.  Terminating GDB."
            os.kill(os.getppid(), signal.SIGTERM)
        except SystemExit:
            raise
        except:
            import traceback
            traceback.print_exc()
            print "Proxy continuing..."

def ProxyServer(gdb_conn, address_file):
    try:
        server = Listener(('localhost', 0))
        open(address_file, 'w').write(json.dumps(server.address))
        gdb_conn.send({'op': 'init', 'port': server.address[1], 'host': server.address[0]})
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except:
        import traceback
        traceback.print_exc()
        print "Aborting proxy"
        return
    while True:
        vim_conn = server.accept()
        ProxyConnection(vim_conn, gdb_conn)
        vim_conn.close()

if __name__ == '__main__':
    import gdb
    gdb_sock, gdb_proxy = Pipe(True)
    exterminator_file = os.environ['EXTERMINATOR_FILE']

    proxy = Process(target=ProxyServer, args=(gdb_proxy, exterminator_file))
    proxy.start()

    gdb_manager = Gdb(gdb_sock, proxy)
    gdb_manager.attach_hooks()

