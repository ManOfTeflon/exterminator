import os, signal, select, json, sys
from multiprocessing import Process, Pipe
from multiprocessing.connection import Listener
sys.path.insert(0, os.path.dirname(__file__))
from gdb_exterminator import Gdb

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
    gdb_sock, gdb_proxy = Pipe(True)
    exterminator_file = os.environ['EXTERMINATOR_FILE']

    proxy = Process(target=ProxyServer, args=(gdb_proxy, exterminator_file))
    proxy.start()

    gdb_manager = Gdb(gdb_sock, proxy)
    gdb_manager.attach_hooks()

