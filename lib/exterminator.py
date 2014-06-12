import os, signal, select, json, time
from multiprocessing import Process, Pipe
from multiprocessing.connection import Listener, Client
from pysigset import suspended_signals

def char_ptr_to_py(value):
    try:
        s = "Cannot decode object"
        for encoding in [ 'utf8', 'ascii' ]:
            try:
                s = '"%s"' % value.string(encoding).encode("unicode-escape")
                break
            except UnicodeDecodeError:
                pass
        return { s : {} }
    except gdb.error as e:
        return { str(e) : {} }
    except gdb.MemoryError as e:
        return { str(e) : {} }

def gdb_to_py(name, value, fullname=None):
    t = value.type
    typename = str(t)

    if fullname is None:
        fullname = name
        name = "%s (%s)" % (name, typename)

    try:
        s = (u"%s" % value).encode('utf8')
    except gdb.error as e:
        return { name: { str(e) : {} } }

    if t.code == gdb.TYPE_CODE_TYPEDEF:
        t = t.strip_typedefs()

    if t.code == gdb.TYPE_CODE_PTR:
        if s == '0x0':
            return { name: { "nullptr": {} } }

        if str(t.target().unqualified()) == 'char':
            return { name: char_ptr_to_py(value) }

        if str(t.target().unqualified()) == 'void':
            return { name: { s : {} } }

        try:
            return gdb_to_py(name, value.dereference(), '(*%s)' % fullname)
        except gdb.error as e:
            return { name: { str(e) : {} } }
    elif t.code == gdb.TYPE_CODE_REF:
        t = t.target()

    if t.code == gdb.TYPE_CODE_STRUCT or t.code == gdb.TYPE_CODE_UNION:
        separator = '.'
        contents = {}
        def struct_to_py(field):
            contents = {}
            for sub_field in field.fields():
                sub_field_name = "%s (%s)" % (sub_field.name, sub_field.type)
                if sub_field.is_base_class:
                    this = { sub_field_name: "static_cast<%s >(%s)" % (sub_field.type, fullname) }
                else:
                    if sub_field.name:
                        this = { sub_field_name: fullname + separator + sub_field.name }
                    else:
                        this = { sub_field_name: struct_to_py(sub_field.type) }
                contents = dict(contents, **this)
            return contents

        return { name: struct_to_py(t) }

    elif t.code == gdb.TYPE_CODE_ARRAY and str(t.target().unqualified()) != 'char':
        size = t.sizeof / t.target().sizeof
        contents = {}
        elem_typename = str(t.target())
        for i in xrange(min(size, 10)):
            elem_name = "[%d]" % (i)
            this = { "%s (%s)" % (elem_name, elem_typename): "%s[%d]" % (fullname, i) }
            contents = dict(contents, **this)
        if size > 10:
            contents["Show 10 more..."] = {}
        return { name: contents }

    else:
        return { name: { s: {} } }

def locals_to_py():
    variables = [ 'this' ]
    variables += [ a.split(' = ', 1)[0] for a in gdb.execute("info args", to_string=True).split('\n')[:-1] ]
    variables += [ l.split(' = ', 1)[0] for l in gdb.execute("info locals", to_string=True).split('\n')[:-1] ]
    contents = {}
    for var in variables:
        try:
            value = gdb.parse_and_eval(var)

        except gdb.error as e:
            if var != 'this':
                contents = dict(contents, **{ var: { str(e): {} } })

        else:
            contents = dict(contents, **gdb_to_py(var, value))
    return contents

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
            os.system('tmux send-keys -t %s "\x1b\x1b:call HistPreserve(\'GdbConnect\')" ENTER' % (self.vim_tmux_pane))

        gdb.execute("set pagination off")

    def attach_hooks(self):
        def on_prompt(prompt):
            with suspended_signals(signal.SIGINT):
                try:
                    self.handle_events()
                    self.goto_selected_frame()
                    self.mark_breakpoints()
                except:
                    import traceback
                    traceback.print_exc()
        gdb.prompt_hook = on_prompt

        def on_cont(event):
            with suspended_signals(signal.SIGINT):
                try:
                    self.filename, self.line = None, None
                    self.mark_breakpoints()
                except:
                    import traceback
                    traceback.print_exc()
        gdb.events.cont.connect(on_cont)

    def dettach_hooks(self):
        gdb.prompt_hook = None

    def vim(self, **kwargs):
        p = dict({'dest': 'vim'}, **kwargs)
        self.sock.send(p)
        if self.vim_tmux_pane and p['dest'] == 'vim' and p['op'] != 'quit':
            os.system('tmux send-keys -t %s "\x1b\x1b:call HistPreserve(\'GdbRefresh\')" ENTER' % (self.vim_tmux_pane))

    def handle_events(self):
        while self.sock.poll():
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
                if c['expr'] == 'auto':
                    print 'info locals'
                    contents = { 'locals': locals_to_py() }
                else:
                    print 'eval ' + c['expr']
                    try:
                        value = gdb.parse_and_eval(c['expr'])

                    except gdb.error as e:
                        contents = { c['expr']: { str(e): {} } }

                    else:
                        contents = gdb_to_py(c['expr'], value)

                if len(contents) == 1:
                    expr, value = contents.items()[0]
                    c['expr'] = expr
                    contents = value
                self.vim(op='response', request_id=c['request_id'], expr=c['expr'], contents=contents)
            elif c['op'] == 'bt':
                print 'bt'
                bt = []
                frame = gdb.newest_frame()
                while frame is not None:
                    filename, line = self.to_loc(frame.find_sal())
                    if filename is not None and line is not None:
                        bt.append((filename, line, str(frame.name())))
                    frame = frame.older()
                self.vim(op='response', request_id=c['request_id'], bt=bt)
            elif c['op'] == 'disable':
                self.disable_breakpoints(*c['loc'])
            elif c['op'] == 'toggle':
                self.toggle_breakpoints(*c['loc'])
            elif c['op'] == 'goto':
                self.toggle_breakpoints(*c['loc'])
            elif c['op'] == 'quit':
                gdb.execute('quit')

    def goto_selected_frame(self):
        try:
            frame = gdb.selected_frame()
        except gdb.error:
            return # no frame selected
        filename, line = self.to_loc(frame.find_sal())
        self.goto_file(filename, line)

    def goto_file(self, filename, line):
        assert (filename is None) == (line is None)
        if filename is None:
            return
        if not os.path.exists(filename):
            return
        if filename == self.filename and line == self.line:
            return
        self.filename = filename
        self.line = line
        self.vim(op='place', num=2, name='dummy', line=line, filename=filename)
        self.vim(op='goto', line=line, filename=filename)

    def to_loc(self, sal):
        if sal is not None and sal.symtab is not None:
            return sal.symtab.filename, int(sal.line)
        else:
            return None, None

    def get_locations(self, breakpoint):
        unparsed, locs = gdb.decode_line(breakpoint.location)
        if locs:
            return [ self.to_loc(loc) for loc in locs if self.to_loc(loc) ]

    def mark_breakpoints(self):
        breakpoints = []
        if gdb.breakpoints() is not None:
            breakpoints = gdb.breakpoints()
        new_breakpoints = {}
        for breakpoint in breakpoints:
            for filename, line in self.get_locations(breakpoint):
                if filename is not None and breakpoint.enabled:
                    new_breakpoints[(filename, line)] = 'breakpoint'
        if self.filename is not None:
            name = 'pc_and_breakpoint' if (self.filename, self.line) in new_breakpoints.keys() else 'just_pc'
            new_breakpoints[(self.filename, self.line)] = name

        old_breakpoints = set(self.breakpoints.keys())
        remove = { key: self.breakpoints[key] for key in old_breakpoints - set(new_breakpoints.keys()) }
        for (filename, line), (num, _) in remove.iteritems():
            self.vim(op='unplace', num=num)
            del self.breakpoints[(filename, line)]

        for (filename, line), name in new_breakpoints.iteritems():
            if (filename, line) not in self.breakpoints:
                self.breakpoints[(filename, line)] = (self.next_breakpoint, name)
                self.vim(op='place', num=self.next_breakpoint, name=name, line=line, filename=filename)
                self.next_breakpoint += 1
            else:
                num, old_name = self.breakpoints[(filename, line)]
                if old_name != name:
                    self.breakpoints[(filename, line)] = (num, name)
                    self.vim(op='replace', num=num, name=name, filename=filename)

    def disable_breakpoints(self, filename, line):
        if gdb.breakpoints() is not None:
            for breakpoint in gdb.breakpoints():
                for old_filename, old_line in self.get_locations(breakpoint):
                    if (old_filename, old_line) == (filename, line):
                        breakpoint.delete()

    def toggle_breakpoints(self, filename, line):
        found = False
        if gdb.breakpoints() is not None:
            for breakpoint in gdb.breakpoints():
                for old_filename, old_line in self.get_locations(breakpoint):
                    if (old_filename, old_line) == (filename, line) and breakpoint.is_valid():
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
        self.request_id = 0
        self.response = {}

    def send_command(self, **kwargs):
        self.request_id += 1
        try:
            self.sock.send(dict(dict({'dest': 'gdb'}, **kwargs), request_id=self.request_id))
        except IOError:
            print "Broken pipe encountered sending to the proxy.  Terminating Exterminator."
            self.quit()
        return self.request_id

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
            elif c['op'] == 'response':
                self.response[c['request_id']] = c
            elif c['op'] == 'place':
                self.vim.command("badd %(filename)s" % c)
                self.vim.command("sign place %(num)s name=%(name)s line=%(line)s file=%(filename)s" % c)
            elif c['op'] == 'replace':
                self.vim.command("sign place %(num)s name=%(name)s file=%(filename)s" % c)
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

    def send_exec(self, comm):
        self.send_command(op='exec', comm=comm)
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
        request_id = self.send_command(op='eval', expr=str(expr))
        return self.get_response(request_id)

    def get_response(self, request_id):
        start = time.time()
        while request_id not in self.response:
            self.send_trap()
            try:
                select.select([self.sock], [], [], 1)
                self.handle_events()
            except select.error:
                pass
            if time.time() - start > 5:
                return None
        response = self.response[request_id]
        del self.response[request_id]
        return response

    def fetch_children(self, expr):
        try:
            return self.eval_expr(expr)['contents']
        except:
            import traceback
            lines = traceback.format_exc().split('\n')
            p = len("%d" % len(lines))
            return { "Python error": { "%0*d: %s" % (p, i, line): {} for i, line in enumerate(lines) } }

    def show_backtrace(self):
        request_id = self.send_command(op='bt')
        response = self.get_response(request_id)
        my_llist = self.vim.List([{ 'filename': filename, 'lnum': line, 'text': contents } for filename, line, contents in response['bt'] ])
        setloclist = self.vim.Function('setloclist')
        setloclist(0, my_llist)
        self.vim.command('lopen')

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

def ProxyConnection(connection_id, vim_conn, gdb_conn):
    while True:
        try:
            for ready in select.select([vim_conn, gdb_conn], [], [])[0]:
                c = ready.recv()
                if c['dest'] == 'proxy':
                    HandleProxyRequest(c)
                elif c['dest'] == 'vim':
                    c['conn'] = connection_id
                    vim_conn.send(c)
                elif c['dest'] == 'gdb':
                    c['conn'] = connection_id
                    gdb_conn.send(c)
                else:
                    print "Packet with unknown dest: " + str(c)
        except (IOError, EOFError):
            print "Broken pipe encountered in the proxy.  Terminating GDB."
            try:
                os.kill(os.getppid(), signal.SIGTERM)
            except:
                pass
            exit(0)
        except SystemExit:
            raise
        except select.error:
            pass
        except:
            import traceback
            print traceback.format_exc()
            print "Proxy continuing..."

def ProxyServer(gdb_conn, address_file):
    connection_id = 0
    try:
        server = Listener(('localhost', 0))
        open(address_file, 'w').write(json.dumps(server.address))
        gdb_conn.send({'op': 'init', 'port': server.address[1], 'host': server.address[0]})
        def exit_proxy(a, b):
            print "GDB has gone away.  Terminating proxy."
            exit(0)
        signal.signal(signal.SIGHUP, exit_proxy)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except:
        import traceback
        traceback.print_exc()
        print "Aborting proxy"
        return
    while True:
        vim_conn = server.accept()
        ProxyConnection(connection_id, vim_conn, gdb_conn)
        vim_conn.close()

if __name__ == '__main__':
    import gdb
    gdb_sock, gdb_proxy = Pipe(True)
    exterminator_file = os.environ['EXTERMINATOR_FILE']

    proxy = Process(target=ProxyServer, args=(gdb_proxy, exterminator_file))
    proxy.start()

    gdb_manager = Gdb(gdb_sock, proxy)
    gdb_manager.attach_hooks()

