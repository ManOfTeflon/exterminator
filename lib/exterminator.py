import os, signal, select, json, sys, errno
# import prctl
from threading import Thread
from multiprocessing import Process, Pipe
from multiprocessing.connection import Listener, Client
sys.path.insert(0, os.path.dirname(__file__))
from protocol import ProtocolSocket

vim_tmux_pane = ''

def output(msg):
    print(str(msg))
    # sys.stderr.write(msg+'\n')
    # sys.stderr.flush()

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
            if state['shutdown'] or os.getppid() == 1:
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
        except IOError as e:
            if e.errno != errno.EINTR:
                break
        except EOFError:
            break
        except SystemExit:
            raise
        except (select.error, DoNotForward, KeyboardInterrupt):
            pass
        except:
            import traceback
            traceback.print_exc()
            output("Proxy continuing...")

    output("Broken pipe encountered in the proxy.  Ending proxy.")
    try:
        if gdb_pid and state['gdb'] == 'managed':
            output("Terminating GDB.")
            os.kill(gdb_pid, signal.SIGTERM)
    except:
        pass
    exit(0)

def ProxyServer(gdb_conn, address_file):
    global vim_tmux_pane
    try:
        connection_id = 0
        state = { 'shutdown': False, 'gdb': 'superior' }
        def _sighup_handler(signum, frame):
            state['shutdown'] = True
        # signal.signal(signal.SIGHUP, _sighup_handler)
        # prctl.prctl(prctl.PDEATHSIG, signal.SIGHUP);

        if 'EXTERMINATOR_TUNNEL' in os.environ:
            # Remote side of an SSH connection
            #
            port = int(os.environ['EXTERMINATOR_TUNNEL'])
            host = '127.0.0.1'
            vim_conn = ProtocolSocket(Client((host, port)))
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
                # signal.signal(signal.SIGHUP, exit_proxy)
                # signal.signal(signal.SIGINT, signal.SIG_IGN)
            except:
                import traceback
                traceback.print_exc()
                output("Aborting proxy")
                return
            while True:
                vim_conn = ProtocolSocket(server.accept())
                ProxyConnection(connection_id, state, vim_conn, gdb_conn)
                vim_conn.close()
    except SystemExit:
        raise
    except:
        import traceback
        output(traceback.format_exc())
        try:
            gdb_conn.send_packet(json.dumps({'op': 'init', 'dest': 'gdb', 'error': True}))
            gdb_conn.close()
        except IOError:
            output('shit')
            pass
        raise

def RunServer():
    server = Listener(('localhost', int(os.environ['EXTERMINATOR_SERVER'])))

    def _ServerThread(conn):
        try:
            ProxyServer(conn, None)
        except SystemExit:
            pass

    while True:
        gdb_conn = ProtocolSocket(server.accept())
        Thread(target=_ServerThread, args=(gdb_conn,)).start()

if __name__ == '__main__':
    exterminator_file = None
    vim_tmux_pane = None
    gdb_pid = None

    if 'EXTERMINATOR_FILE' in os.environ:
        exterminator_file = os.environ['EXTERMINATOR_FILE']
    if 'VIM_TMUX_PANE' in os.environ:
        vim_tmux_pane = os.environ['VIM_TMUX_PANE']

    if 'EXTERMINATOR_SERVER' in os.environ:
        # Local side of an SSH connection
        #
        RunServer()
        exit(0)

    gdb_pid = os.getpid()
    gdb_sock, gdb_proxy = Pipe(True)

    proxy = Process(target=ProxyServer, args=(ProtocolSocket(gdb_proxy), exterminator_file))
    proxy.daemon = True
    proxy.start()

    from gdb_exterminator import Gdb
    try:
        gdb_manager = Gdb(ProtocolSocket(gdb_sock))
    except (IOError, EOFError):
        pass
    else:
        gdb_manager.attach_hooks()

