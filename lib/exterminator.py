import os, signal, select, json, sys, base64, prctl
import paramiko
from multiprocessing import Process, Pipe
from multiprocessing.connection import Listener
sys.path.insert(0, os.path.dirname(__file__))
from protocol import StdioSocket, ProtocolSocket, SshSocket

vim_tmux_pane = ''

def output(msg):
    sys.stderr.write(msg+'\n')
    sys.stderr.flush()

def pts(msg):
    global gdb_pid
    prefix = "gdb proxy: " if gdb_pid else "vim proxy: "
    open('/dev/pts/10', 'wb').write(prefix+str(msg)+'\n')

class DoNotForward(Exception):
    pass

def HandleProxyRequest(c):
    global vim_tmux_pane, gdb_pid
    if c['op'] == 'trap':
        if c['target'] == 'gdb':
            if gdb_pid:
                os.kill(gdb_pid, signal.SIGINT)
        elif c['target'] == 'vim':
            if vim_tmux_pane:
                os.system('tmux send-keys -t %s "\x1b\x1b:call HistPreserve(\'GdbRefresh\')" ENTER' % (vim_tmux_pane))
        else:
            output("Proxy trap with unknown target: " + str(c))
        return
    elif c['op'] == 'quit':
        exit(0)
    elif c['op'] == 'print':
        output(c['msg'])
    elif c['op'] == 'tmux_pane':
        if not vim_tmux_pane:
            vim_tmux_pane = c['pane']
    else:
        output("Proxy packet with unknown op: " + str(c))
    raise DoNotForward()

def ProxyConnection(connection_id, state, vim_conn, gdb_conn):
    global gdb_pid
    while True:
        try:
            if state['shutdown']:
                output("GDB has terminated.  Ending proxy")
                exit(0)
            for ready in ProtocolSocket.select([vim_conn, gdb_conn], timeout=1):
                c_data = ready.recv_packet()
                c = json.loads(c_data.decode('utf-8'))
                other_conn = vim_conn if ready is gdb_conn else gdb_conn
                if c['dest'] == 'proxy':
                    HandleProxyRequest(c)
                elif c['dest'] == 'vim':
                    c['conn'] = connection_id
                elif c['dest'] == 'gdb':
                    c['conn'] = connection_id
                else:
                    output("Packet with unknown dest: " + str(c))
                other_conn.send_packet(json.dumps(c))
        except (IOError, EOFError):
            output("Broken pipe encountered in the proxy.  Ending proxy.")
            try:
                if gdb_pid and state['gdb'] == 'managed':
                    output("Terminating GDB.")
                    os.kill(gdb_pid, signal.SIGTERM)
            except:
                pass
            exit(0)
        except SystemExit:
            raise
        except (select.error, DoNotForward):
            pass
        except:
            import traceback
            traceback.print_exc()
            output("Proxy continuing...")

def ProxyServer(gdb_conn, address_file):
    global vim_tmux_pane
    connection_id = 0
    state = { 'shutdown': False, 'gdb': 'superior' }
    def _sighup_handler(signum, frame):
        state['shutdown'] = True
    signal.signal(signal.SIGHUP, _sighup_handler)
    prctl.prctl(prctl.PDEATHSIG, signal.SIGHUP);
    if 'EXTERMINATOR_TUNNEL' in os.environ:
        tunnel = json.loads(base64.b64decode(os.environ['EXTERMINATOR_TUNNEL']))

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(**tunnel['ssh'])

        transport = ssh.get_transport()
        channel = transport.open_session()

        command = "DISPLAY='%(display)s' EXTERMINATOR_SSH_PROXY= python -u %(file)s" % tunnel
        channel.exec_command(command)
        channel.send_bytes = channel.send
        channel.recv_bytes = channel.recv

        vim_conn = ProtocolSocket(SshSocket(channel))
        ProxyConnection(connection_id, state, vim_conn, gdb_conn)
    else:
        try:
            server = Listener(('localhost', 0))
            if address_file:
                state['gdb'] = 'managed'
                open(address_file, 'w').write(json.dumps(server.address))
            else:
                output("Waiting for connection on %s:%d" % server.address)
                command = 'GdbConnect %s:%d' % server.address
                if 'DISPLAY' in os.environ:
                    output("Connect in vim using '%s' (in selection buffer)" % command)
                    os.system("echo -n '%s' | xsel -i" % command)
                else:
                    output("Connect in vim using '%s'" % command)
            if vim_tmux_pane:
                os.system('tmux send-keys -t %s "\x1b\x1b:call HistPreserve(\'GdbConnect\')" ENTER' % (vim_tmux_pane))
            gdb_conn.send_packet(json.dumps({'dest': 'gdb', 'op': 'init', 'port': server.address[1], 'host': server.address[0]}).encode('utf-8'))
            def exit_proxy(a, b):
                output("GDB has gone away.  Terminating proxy.")
                if vim_tmux_pane:
                    os.system('tmux send-keys -t %s "\x1b\x1b:call HistPreserve(\'GdbRefresh\')" ENTER' % (vim_tmux_pane))
                exit(0)
            signal.signal(signal.SIGHUP, exit_proxy)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except:
            import traceback
            traceback.print_exc()
            output("Aborting proxy")
            return
        while True:
            vim_conn = ProtocolSocket(server.accept())
            ProxyConnection(connection_id, state, vim_conn, gdb_conn)
            vim_conn.close()

if __name__ == '__main__':
    exterminator_file = None
    vim_tmux_pane = None
    gdb_pid = None
    if 'EXTERMINATOR_SSH_PROXY' in os.environ:
        sock = ProtocolSocket(StdioSocket(sys.stdin, sys.stdout))
        def output_over_ssh(msg):
            sock.send_packet(json.dumps({'dest': 'proxy', 'op': 'print', 'msg': msg}))
        output = output_over_ssh
        ProxyServer(sock, None)
    else:
        if 'EXTERMINATOR_FILE' in os.environ:
            exterminator_file = os.environ['EXTERMINATOR_FILE']
        if 'VIM_TMUX_PANE' in os.environ:
            vim_tmux_pane = os.environ['VIM_TMUX_PANE']
        gdb_pid = os.getpid()
        gdb_sock, gdb_proxy = Pipe(True)

        proxy = Process(target=ProxyServer, args=(ProtocolSocket(gdb_proxy), exterminator_file))
        proxy.daemon = True
        proxy.start()

        from gdb_exterminator import Gdb
        gdb_manager = Gdb(ProtocolSocket(gdb_sock))
        gdb_manager.attach_hooks()

