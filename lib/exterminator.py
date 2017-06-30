import os, signal, select, json, sys, errno
# import prctl
from threading import Lock, Thread
from multiprocessing import Pipe
from multiprocessing.connection import Listener, Client
sys.path.insert(0, os.path.dirname(__file__))
from protocol import ProtocolSocket, MalformedPacket

vim_tmux_pane = ''

def output(msg):
    print(str(msg))
    # sys.stderr.write(msg+'\n')
    # sys.stderr.flush()
    # pts(msg)

def pts(msg):
    global gdb_pid
    prefix = "gdb proxy: " if gdb_pid else "vim proxy: "
    open('/dev/pts/10', 'wb').write(prefix+str(msg)+'\n')

class DoNotForward(Exception):
    pass

class TimeoutException(Exception):
    pass

def accept_timeout(server, timeout=0):
    sock = server._listener._socket
    while True:
        try:
            if len(select.select([sock], [sock], [sock], timeout)):
                return server.accept()
            break
        except select.error as (e, m):
            if e != errno.EINTR:
                raise
    raise TimeoutException()

def AcceptLoop(server, action):
    while True:
        try:
            conn = ProtocolSocket(accept_timeout(server, timeout=1))

            Thread(target=action, args=(conn,)).start()
        except TimeoutException:
            if g_state['shutdown']:
                output("Shutting down proxy server accept loop")
                break

def HandleProxyRequest(c):
    global vim_tmux_pane, gdb_pid, g_state
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
        g_state['shutdown'] = True
    elif c['op'] == 'print':
        output(c['msg'])
    elif c['op'] == 'tmux_pane':
        if not vim_tmux_pane:
            vim_tmux_pane = c['pane']
    else:
        output("Proxy packet with unknown op: " + str(c))
    raise DoNotForward()

def ProxyConnection(name, conns):
    global gdb_pid
    in_conn = conns[name]
    try:
        while True:
            try:
                if g_state['shutdown'] or os.getppid() == 1:
                    output("GDB has terminated.  Ending proxy connection to %s" % name)
                    gdb_pid = None
                    exit(0)
                for ready in ProtocolSocket.select([in_conn], timeout=1):
                    c = ready.recv_packet()
                    dest_conn = None
                    if c['dst'] == 'proxy':
                        HandleProxyRequest(c)
                    else:
                        c['src'] = name
                        try:
                            dest_conn = conns[c['dst']]
                        except KeyError:
                            # Swallow packets intended for vim if no vim is
                            # connected
                            #
                            if c['dst'] != "vim":
                                output("Packet with unknown destination: " + str(c))

                    if dest_conn:
                        dest_conn.send_packet(**c)

            except IOError as e:
                output("IOError(%s)" % str(e.errno))
                if e.errno != errno.EINTR:
                    break
            except EOFError:
                output("Proxy connection to %s has ended gracefully." % name)
                break
            except MalformedPacket as e:
                output("Malformed packet: %s" % e)
            except SystemExit:
                raise
            except (select.error, DoNotForward, KeyboardInterrupt):
                pass
            except:
                import traceback
                traceback.print_exc()
                output("Proxy continuing...")

    finally:
        try:
            if gdb_pid and g_state['gdb'] == 'managed' and name in ("vim", "gdb"):
                output("Terminating GDB.")
                os.kill(gdb_pid, signal.SIGTERM)
        except:
            pass

def ProxyServer(gdb_conn, address_file):
    global vim_tmux_pane, g_state
    try:
        if 'EXTERMINATOR_TUNNEL' in os.environ:
            # Remote side of an SSH connection
            #
            port = int(os.environ['EXTERMINATOR_TUNNEL'])
            host = '127.0.0.1'
            vim_conn = ProtocolSocket(Client((host, port)))
            conns = { 'vim': vim_conn, 'gdb': gdb_conn }
            Thread(target=ProxyConnection, args=('vim', conns)).start()
            Thread(target=ProxyConnection, args=('gdb', conns)).start()
        else:
            try:
                server = Listener(('localhost', 0))
                if address_file:
                    g_state['gdb'] = 'managed'
                    open(address_file, 'w').write(json.dumps(server.address))
                else:
                    output("Proxy server is running on %s:%d" % server.address)
                    command = 'GdbConnect %s:%d' % server.address
                    if 'DISPLAY' in os.environ:
                        output("Connect in vim using '%s' (in selection buffer)" % command)
                        os.system("echo -n '%s' | xsel -i" % command)
                    else:
                        output("Connect in vim using '%s'" % command)
                if vim_tmux_pane:
                    os.system('tmux send-keys -t %s "\x1b\x1b:call HistPreserve(\'GdbConnect\')" ENTER' % (vim_tmux_pane))
                gdb_conn.send_packet(dst='gdb', op='init', port=server.address[1], host=server.address[0])
            except:
                output("Error occurred during proxy server initialization")
                import traceback
                traceback.print_exc()
                output("Aborting proxy")
                return

            conns = { 'gdb': gdb_conn }
            Thread(target=ProxyConnection, args=('gdb', conns)).start()

            conns_lock = Lock()

            def _Thread(conn):
                try:
                    name = conn.recv_op('name')['name']
                    with conns_lock:
                        if name in conns:
                            output("Attempt to create duplicate connection to %s" % name)
                            return
                        conns[name] = conn
                    ProxyConnection(name, conns)
                except (EOFError, IOError, MalformedPacket) as e:
                    output("Failed to receive name packet: %s" % e)
                    return
                finally:
                    conn.close()

            AcceptLoop(server, _Thread)

    except SystemExit:
        raise
    except:
        import traceback
        output(traceback.format_exc())
        try:
            gdb_conn.send_packet(op='init', dst='gdb', error=True)
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

    AcceptLoop(server, _ServerThread)

if __name__ == '__main__':
    exterminator_file = None
    vim_tmux_pane = None
    gdb_pid = None

    g_state = { 'shutdown': False, 'gdb': 'superior' }
    def _sighup_handler(signum, frame):
        global g_state
        g_state['shutdown'] = True
    signal.signal(signal.SIGHUP, _sighup_handler)

    if 'EXTERMINATOR_FILE' in os.environ:
        exterminator_file = os.environ['EXTERMINATOR_FILE']
    if 'VIM_TMUX_PANE' in os.environ:
        vim_tmux_pane = os.environ['VIM_TMUX_PANE']

    if 'EXTERMINATOR_SERVER' in os.environ:
        # Local side of an SSH connection
        #
        try:
            RunServer()
        finally:
            exit(0)

    gdb_pid = os.getpid()
    gdb_sock, gdb_proxy = Pipe(True)

    if os.fork() == 0:
        try:
            import ctypes

            libc = ctypes.cdll.LoadLibrary("libc.so.6")
            name = "exterminator_proxy"
            buff = ctypes.create_string_buffer(len(name) + 1)
            buff.value = name
            libc.prctl(15, ctypes.byref(buff), 0, 0, 0)

            ProxyServer(ProtocolSocket(gdb_proxy), exterminator_file)
        finally:
            exit(0)
    else:
        from gdb_exterminator import Gdb
        try:
            gdb_manager = Gdb(ProtocolSocket(gdb_sock))
        except (IOError, EOFError):
            pass
        else:
            gdb_manager.attach_hooks()

