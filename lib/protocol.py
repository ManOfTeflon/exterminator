import struct, select, signal, socket, threading, json
from pysigset_exterminator import suspended_signals

class MalformedPacket(BaseException):
    def __init__(self, packet, reason):
        self._packet = packet
        self._reason = reason

    def __repr__(self):
        return "MalformedPacket(packet=\"%s\", reason=\"%s\")" % (
            repr(self._packet).replace('"', '\\"'),
            repr(self._reason).replace('"', '\\"'))

    def __str__(self):
        return repr(self)

class SshSocket(object):
    def __init__(self, channel):
        self._channel = channel

    def send_bytes(self, msg):
        self._channel.send(msg)

    def recv_bytes(self, size):
        msg = ''
        while size > 0:
            try:
                part = self._channel.recv(size)
            except socket.timeout:
                continue
            if len(part) == 0:
                raise EOFError
            size -= len(part)
            msg += part
        return msg

    def fileno(self):
        return self._channel.fileno()

    def poll(self):
        return self._channel.recv_ready()

class StdioSocket(object):
    def __init__(self, infile, outfile):
        self._infile = infile
        self._outfile = outfile
        self._eof = False

    def send_bytes(self, msg):
        self._outfile.write(msg)
        self._outfile.flush()

    def recv_bytes(self, size):
        if self._eof:
            raise EOFError
        msg = self._infile.read(size)
        if len(msg) < size:
            self._eof = True
        if len(msg) == 0:
            raise EOFError
        return msg

    def fileno(self):
        return self._infile.fileno()

    def poll(self):
        return True

class ProtocolSocket(object):
    def __init__(self, sock):
        self._sock = sock
        self._lock = threading.Lock()

    def send_packet(self, **kwargs):
        assert('dst' in kwargs.keys() and 'op' in kwargs.keys())
        msg = json.dumps(kwargs).encode('utf8')
        with self._lock:
            with suspended_signals(signal.SIGINT):
                self._sock.send_bytes(struct.pack('I', len(msg)))
                self._sock.send_bytes(bytes(msg))

    def recv_packet(self):
        with self._lock:
            with suspended_signals(signal.SIGINT):
                size = int(struct.unpack('I', self._sock.recv_bytes(4))[0])
                r = bytes(self._sock.recv_bytes(size))

                try:
                    r = r.decode('utf-8')
                    r = json.loads(r)
                    assert('dst' in r.keys() and 'op' in r.keys())
                except UnicodeDecodeError:
                    raise MalformedPacket(r, "failed to decode unicode")
                except ValueError:
                    raise MalformedPacket(r, "failed to parse json")
                except AssertionError:
                    raise MalformedPacket(r, "packet is lacking 'dst' or 'op' field")

                return r

    def recv_op(self, opname):
        c = self.recv_packet()
        if c['op'] != opname:
            raise MalformedPacket(c, "expected op=\"%s\"" % (str(opname).replace('"', '\\"')))
        return c

    def fileno(self):
        return self._sock.fileno()

    def poll(self, timeout=0):
        ready = select.select([self._sock], [], [], timeout)[0]
        return len(ready) > 0

    def close(self):
        self._sock.close()

    @staticmethod
    def select(socks, timeout=None):
        ready = select.select(socks, [], [], timeout)[0]
        return filter(lambda sock: sock.poll(), ready)

