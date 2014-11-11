import json
import os
import time
import select
from multiprocessing.connection import Client

class RemoteGdb(object):
    def __init__(self, vim, host, port):
        self.vim = vim
        self.sock = Client((host, port))
        self.request_id = 0
        self.response = {}

    def send_command(self, **kwargs):
        self.request_id += 1
        try:
            self.sock.send_bytes(json.dumps(dict(dict({'dest': 'gdb'}, **kwargs), request_id=self.request_id)).encode('utf-8'))
        except IOError:
            print "Broken pipe encountered sending to the proxy.  Terminating Exterminator."
            self.quit()
        return self.request_id

    def handle_events(self):
        if not self.sock.poll():
            return
        while True:
            try:
                c = json.loads(self.sock.recv_bytes().decode('utf-8'))
            except (IOError, EOFError):
                print "Lost connection to GDB"
                self.quit()
                return
            if c['op'] == 'goto':
                window = self.find_window('navigation')
                if window is None:
                    self.claim_window('navigation')
                c['filename'] = os.path.abspath(c['filename'])
                self.vim.command('badd %(filename)s' % c)
                self.vim.command("buffer %(filename)s" % c)
                self.vim.command("%(line)s" % c)
                self.vim.command("%(line)skP" % c)
                self.vim.command("norm zz")
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
            elif c['op'] == 'refresh':
                GDBPlugin = self.vim.bindeval('g:NERDTreeGDBPlugin')
                NERDTreeFromJSON = self.vim.Function('NERDTreeFromJSON')
                NERDTreeFromJSON(c['expr'], GDBPlugin)
            elif c['op'] == 'place':
                c['filename'] = os.path.abspath(c['filename'])
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

    def send_exec(self, comm):
        self.send_command(op='exec', comm=comm)
        self.send_trap()

    def disable_break(self, filename, line):
        self.send_command(op='disable', loc=(filename, line))
        self.send_trap()

    def toggle_break(self, filename, line):
        self.send_command(op='toggle', loc=(filename, line))
        self.send_trap()

    def continue_until(self, filename, line):
        self.send_command(op='until', loc=(filename, line))
        self.send_trap()

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
            v = self.eval_expr(expr)
            return [ v['expr'], v['contents'] ]
        except:
            import traceback
            lines = traceback.format_exc().split('\n')
            p = len("%d" % len(lines))
            return [ "Python client error", { "%0*d: %s" % (p, i, line): {} for i, line in enumerate(lines) } ]

    def track_expr(self, expr):
        if expr is not None:
            GDBPlugin = self.vim.bindeval('g:NERDTreeGDBPlugin')
            NERDTreeFromJSON = self.vim.Function('NERDTreeFromJSON')
            NERDTreeFromJSON(expr, GDBPlugin)
        self.send_command(op='track', expr=expr)

    def show_backtrace(self):
        request_id = self.send_command(op='bt')
        response = self.get_response(request_id)
        my_llist = self.vim.List([{ 'filename': filename, 'lnum': line, 'text': contents } for filename, line, contents in response['bt'] ])
        setloclist = self.vim.Function('setloclist')
        setloclist(0, my_llist)
        self.vim.command('lopen')
        self.vim.command('GdbBindBufferToFrame')

    def claim_window(self, window_name):
        self.vim.command('let w:mandrews_output_window = "%s"' % window_name)

    def find_window(self, window_name, new_command=None):
        winnr = int(self.vim.eval("winnr()"))
        while True:
            if int(self.vim.eval("exists('w:mandrews_output_window')")) > 0:
                if str(self.vim.eval("w:mandrews_output_window")) == window_name:
                    break
            self.vim.command("wincmd w")
            if winnr == int(self.vim.eval("winnr()")):
                if new_command is not None:
                    self.vim.command(new_command)
                    self.claim_window(window_name)
                    break
                return
        return self.vim.current.window

