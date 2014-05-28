import os, signal, select
from multiprocessing import Process, Pipe
from multiprocessing.connection import Listener, Client

class Gdb(object):
    def __init__(self, sock, proxy):
        self.vimserver = str(gdb.parse_and_eval("$vim"))
        self.sock = sock
        self.next_breakpoint = 2
        self.proxy = proxy

        self.breakpoints = { }
        self.filename = ''
        self.line = 0

        hello = self.sock.recv()
        assert hello['op'] == 'init', str(hello)

        self.port = hello['port']

        self.vim('let g:GDB_PORT=%d' % self.port)
        self.vim('python InitRemoteGdb()')

    def attach_hooks(self):
        def on_prompt(prompt):
            self.handle_events()
            try:
                self.goto_frame(gdb.selected_frame())
                self.mark_breakpoints()
            except gdb.error:
                pass
            except:
                raise
        gdb.prompt_hook = on_prompt

        def kill_server(e):
            self.vim('sign unplace *')
            self.proxy.terminate()
        gdb.events.exited.connect(kill_server)

    def vim(self, cmd):
        os.system(("vim --servername " + self.vimserver + " --remote-send '<esc><esc>:" + cmd + "<cr>'"))

    def handle_events(self):
        if not self.sock.poll():
            return
        while True:
            c = self.sock.recv()
            if c['op'] == 'exec':
                try:
                    gdb.execute(c['comm'])
                except gdb.error as e:
                    print str(e)
            elif c['op'] == 'eval':
                try:
                    c['response'] = gdb.execute(c['comm'], to_string=True)
                except gdb.error as e:
                    c['response'] = None
                    print str(e)
                c['dest'] = 'vim'
                self.sock.send(c)
            elif c['op'] == 'disable':
                self.disable_breakpoints(*c['loc'])
            elif c['op'] == 'toggle':
                self.toggle_breakpoints(*c['loc'])
            elif c['op'] == 'goto':
                self.toggle_breakpoints(*c['loc'])
            elif c['op'] == 'goto':
                gdb.execute('quit')
            if not self.sock.poll():
                return

    def goto_frame(self, frame):
        filename, line = self.to_loc(frame.find_sal())
        self.goto_file(filename, line)
        self.vim("%(line)skP" % locals())

    def goto_file(self, filename, line):
        if not os.path.exists(filename):
            return
        if filename == self.filename and line == self.line:
            return
        self.filename = filename
        self.line = line
        servername = gdb.parse_and_eval("$vim")
        os.system("vim --servername %(servername)s --remote +%(line)s %(filename)s" % locals())

    def to_loc(self, sal):
        return sal.symtab.filename, int(sal.line)

    def get_locations(self, breakpoint):
        unparsed, locs = gdb.decode_line(breakpoint.location)
        if locs:
            return [ self.to_loc(loc) for loc in locs ]

    def mark_breakpoints(self):
        if gdb.breakpoints() is None:
            self.vim("sign unplace *")
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
            self.vim("sign unplace %d" % self.breakpoints[(filename, line)])
            del self.breakpoints[(filename, line)]

        for filename, line in add:
            print filename, line
            self.breakpoints[(filename, line)] = self.next_breakpoint
            self.vim("badd %(filename)s" % locals())
            self.vim("sign place %d name=breakpoint line=%s file=%s" % (self.next_breakpoint, line, filename))
            self.next_breakpoint += 1

        sign = 'pc_and_breakpoint' if (self.filename, self.line) in self.breakpoints.keys() else 'just_pc'
        self.vim("badd %s" % self.filename)
        self.vim("sign unplace 1")
        self.vim("sign place 1 name=%s line=%s file=%s" % (sign, self.line, self.filename))

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
    def __init__(self, vim):
        self.vim = vim
        self.sock = Client(("localhost", int(vim.eval('g:GDB_PORT'))))

    def send_command(self, **kwargs):
        self.sock.send(dict({'dest': 'gdb'}, **kwargs))

    def receive_response(self):
        return self.sock.recv()['response']

    def send_trap(self):
        self.send_command(dest='proxy', op='trap')

    def send_quit(self):
        self.send_command(op='quit')

    def send_continue(self):
        self.send_command(op='exec', comm='continue')
        self.send_trap()

    def send_next(self):
        self.send_command(op='exec', comm='next')
        self.send_trap()

    def send_step(self):
        self.send_command(op='exec', comm='step')
        self.send_trap()

    def send_break(self, filename, line):
        self.send_command(op='exec', comm='break %s:%d' % (filename, line))
        self.send_trap()

    def disable_break(self, filename, line):
        self.send_command(op='disable', loc=(filename, line))
        self.send_trap()

    def toggle_break(self, filename, line):
        self.send_command(op='toggle', loc=(filename, line))
        self.send_trap()

    def continue_until(self, filename, line):
        self.send_command(op='goto', loc=(filename, line))
        self.send_trap()

    def eval_expr(self, expr):
        self.send_command(op='eval', comm='print ' + str(expr))
        self.send_trap()
        return self.receive_response()

def HandleProxyRequest(c):
    if c['op'] == 'trap':
        os.kill(os.getppid(), signal.SIGINT)
    else:
        print "Proxy packet with unknown op: " + str(c)

def ProxyConnection(vim_conn, gdb_conn):
    try:
        while True:
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
        os.kill(os.getppid(), signal.SIGTERM)
    except:
        import traceback
        traceback.print_exc()
        pass

def ProxyServer(gdb_conn):
    try:
        server = Listener(('localhost', 0))
        gdb_conn.send({'op': 'init', 'port': server.address[1]})
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        while True:
            vim_conn = server.accept()
            ProxyConnection(vim_conn, gdb_conn)
            vim_conn.close()
    except:
        import traceback
        traceback.print_exc()
        gdb_conn.send({'op': 'abort'})

if __name__ == '__main__':
    import gdb
    gdb_sock, gdb_proxy = Pipe(True)

    proxy = Process(target=ProxyServer, args=(gdb_proxy,))
    proxy.start()

    gdb_manager = Gdb(gdb_sock, proxy)
    gdb_manager.attach_hooks()

