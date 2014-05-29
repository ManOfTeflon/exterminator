import os, signal, select, json
from multiprocessing import Process, Pipe
from multiprocessing.connection import Listener, Client

class Gdb(object):
    def __init__(self, sock, proxy):
        try:
            self.servername = os.environ['VIM_SERVER']
        except KeyError:
            self.servername = None
        self.sock = sock
        self.next_breakpoint = 2
        self.proxy = proxy

        self.breakpoints = { }
        self.filename = ''
        self.line = 0

        hello = self.sock.recv()
        assert hello['op'] == 'init', str(hello)

        self.port = hello['port']
        if self.servername:
            os.system('vim --servername %s --remote-send "<esc><esc>:python InitRemoteGdb(\'localhost\', %d)<cr>"' % (self.servername, self.port))

    def attach_hooks(self):
        def on_prompt(prompt):
            self.handle_events()
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

    def vim(self, **kwargs):
        self.sock.send(dict({'dest': 'vim'}, **kwargs))
        if self.servername:
            os.system('vim --servername %s --remote-send "<esc><esc>:python HandleEvents()<cr>"' % self.servername)

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
                    contents=gdb.execute(c['expr'], to_string=True)
                except gdb.error as e:
                    contents = str(e)
                print contents
                self.vim(op='disp', expr=c['expr'], contents=contents)
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
        self.sock.send(dict({'dest': 'gdb'}, **kwargs))

    def handle_events(self):
        if not self.sock.poll():
            return
        while True:
            try:
                c = self.sock.recv()
            except EOFError:
                self.vim.gdb = None
                return
            if c['op'] == 'goto':
                window = self.find_window('navigation')
                if window is None:
                    self.claim_window('navigation')
                self.vim.command("e +%(line)s %(filename)s" % c)
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
            elif c['op'] == 'place':
                self.vim.command("badd %(filename)s" % c)
                self.vim.command("sign place %(num)s name=%(name)s line=%(line)s file=%(filename)s" % c)
            elif c['op'] == 'unplace':
                self.vim.command("sign unplace %(num)s" % c)
            elif c['op'] == 'quit':
                self.vim.command("sign unplace *")
                winnr = int(self.vim.eval("winnr()"))
                window = self.find_window('display')
                if window is not None:
                    self.vim.command("q")
                self.vim.command("%swincmd w" % winnr)
            if not self.sock.poll():
                return

    def send_trap(self):
        self.send_command(dest='proxy', op='trap')

    def send_quit(self):
        self.send_command(op='quit')

    def send_continue(self):
        self.send_command(op='exec', comm='continue')
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
        self.send_command(op='eval', expr='print ' + str(expr))
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
    if c['op'] == 'quit':
        exit(0)
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

def ProxyServer(gdb_conn, address_file):
    try:
        server = Listener(('localhost', 0))
        open(address_file, 'w').write(json.dumps(server.address))
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
    exterminator_file = os.environ['EXTERMINATOR_FILE']

    proxy = Process(target=ProxyServer, args=(gdb_proxy, exterminator_file))
    proxy.start()

    gdb_manager = Gdb(gdb_sock, proxy)
    gdb_manager.attach_hooks()

