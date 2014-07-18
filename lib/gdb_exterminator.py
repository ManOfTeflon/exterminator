import os
import gdb
import signal
from pysigset import suspended_signals

from gdb_values import gdb_to_py, locals_to_py

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
        self.refresh_expr = False
        self.expr = None
        self.last_frame = None

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
                    self.send_expr()
                except:
                    import traceback
                    traceback.print_exc()
                finally:
                    self.signal()
        gdb.prompt_hook = on_prompt

        def on_cont(event):
            with suspended_signals(signal.SIGINT):
                try:
                    print 'cont'
                    self.refresh_expr = True
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

    def signal(self):
        if self.vim_tmux_pane:
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
                    c['expr'], contents = contents.items()[0]
                self.vim(op='response', request_id=c['request_id'], expr=c['expr'], contents=contents)
            elif c['op'] == 'bt':
                print 'bt'
                bt = []
                frame = gdb.newest_frame()
                while frame is not None:
                    filename, line = self.to_loc(frame.find_sal())
                    if filename is None or line is None:
                        filename = ""
                        line = 0
                        name = "Unknown"
                    else:
                        name = frame.name()
                    bt.append((filename, line, name))
                    frame = frame.older()
                self.vim(op='response', request_id=c['request_id'], bt=bt)
            elif c['op'] == 'disable':
                self.disable_breakpoints(*c['loc'])
            elif c['op'] == 'toggle':
                self.toggle_breakpoints(*c['loc'])
            elif c['op'] == 'until':
                self.continue_until(*c['loc'])
            elif c['op'] == 'track':
                self.expr = c['expr']
            elif c['op'] == 'quit':
                gdb.execute('quit')

    def send_expr(self):
        try:
            if self.last_frame != gdb.selected_frame():
                print 'new frame'
                self.refresh_expr = True
                self.last_frame = gdb.selected_frame()
        except:
            pass

        if self.expr is not None and self.refresh_expr:
            self.refresh_expr = False
            self.vim(op='refresh', expr=self.expr)

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
        gdb.execute("until %s:%d" % (filename, line))

