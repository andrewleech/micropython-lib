#!/usr/bin/env python3
# This desktop tool splits the serial port mux stram from mpy into multiple local tcp sockets.
#

import argparse, select, socket, time
import asyncio, serial_asyncio
import serial, serial.tools.list_ports


REQUEST_TIMEOUT = 0.5
TRACE_SERIAL_DATA = True
BUF_SIZE = 500

# TODO(dpg): is this the best/right way to detect UNIX?
if hasattr(socket, "AF_UNIX"):
    UNIX = True
else:
    UNIX = False


class DeviceNotFound(SystemExit):
    def __str__(self):
        return "Device not found"


def find_connected_boards():
    """
    Returns name of com ports where hint is part of port description
    :return: list of string representing com ports
    """
    ports = list(serial.tools.list_ports.comports())
    connected = []
    for p in ports:
        device = p.device
        if UNIX:
            # On OSX, returns the CallOut device by default (/dev/cu.usbmodem...)
            # But we need the DialIn device instead (/dev/tty.usbmodem...)
            device = device.replace("/cu.", "/tty.", 1)
        connected.append((p, device))
    return connected


def find_device(device_pattern=None):
    # Search for devices that have the correct VID/PID, and optionally have
    # device_pattern in their name.
    devices = []
    for port, name in find_connected_boards():
        if name.find(device_pattern) != -1:
            devices.append((port, name))

    if not devices:
        raise DeviceNotFound

    # In development mode the device has 2x VCP ports, one for the REPL and one for the
    # HTTP server.  The HTTP server is on the second interface (second set of endpoints)
    # so try to determine that automatically.
    for port, name in devices:
        if port.location and port.location.endswith(".2"):
            return name

    # Fallback to just the first device in the list.
    return devices[0][1]


# class SerialProtocol(asyncio.Protocol):
#     def __init__(self) -> None:
#         self.tcp_writer: asyncio.StreamWriter = None

#     def connection_made(self, transport):
#         self.transport = transport
#         print('port opened', transport)
#         # transport.serial.rts = False  # You can manipulate Serial object via transport
#         # transport.write(b'Hello, World!\n')  # Write serial data via transport

#     def data_received(self, data):
#         print('serial received', repr(data))
#         if self.tcp_writer is not None:
#             self.tcp_writer.write(data)


#         # if b'\n' in data:
#         #     self.transport.close()

#     def connection_lost(self, exc):
#         print('port closed')
#         self.transport.loop.stop()

#     def pause_writing(self):
#         print('pause writing')
#         print(self.transport.get_write_buffer_size())

#     def resume_writing(self):
#         print(self.transport.get_write_buffer_size())
#         print('resume writing')


# class SocketProtocol(asyncio.Protocol):
#     def __init__(self) -> None:
#         self.ser_writer: asyncio.StreamWriter = None

#     def connection_made(self, transport):
#         self.transport = transport
#         print('port opened', transport)
#         # transport.serial.rts = False  # You can manipulate Serial object via transport
#         # transport.write(b'Hello, World!\n')  # Write serial data via transport

#     def data_received(self, data):
#         print('serial received', repr(data))

#         if self.ser_writer is not None:
#             self.ser_writer.write(data)

#         # if b'\n' in data:
#         #     self.transport.close()

#     def connection_lost(self, exc):
#         print('port closed')
#         self.transport.loop.stop()

#     def pause_writing(self):
#         print('pause writing')
#         print(self.transport.get_write_buffer_size())

#     def resume_writing(self):
#         print(self.transport.get_write_buffer_size())
#         print('resume writing')


# class SocketSerialAdaptor:
#     def __init__(self):
#         pass

#     # async def socket_forwarder(sock_from, sock_to):
#     #     sr = asyncio.StreamReader(sock_from)
#     #     st = asyncio.StreamReader(sock_to)
#     #     while True:
#     #         chunk = await sr.read()
#     #         written = await st.write(chunk)

#     def _serial_read_thread(self):
#         self.serial_sock_rx, self.serial_sock_tx = socket.socketpair()
#         while True:
#             buf = self.serial.read(self.serial.inWaiting() or 1)
#             if buf:
#                 self.serial_sock_tx.sendall(buf)

#     def open_serial(self, path):
#         self.serial = serial.Serial(
#             port=path,
#             baudrate=115200,
#             timeout=REQUEST_TIMEOUT,
#         )
#         time.sleep(0.1)
#         while self.serial.inWaiting():
#             self.serial.read(self.serial.inWaiting())
#             time.sleep(0.1)
#         _thread.start_new_thread(self._serial_read_thread, ())

#     def serve(self, host, port):
#         ai = socket.getaddrinfo(host, port)[0]
#         sock = socket.socket()
#         sock.setblocking(False)
#         sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#         sock.bind(ai[-1])
#         sock.listen()
#         # Accept incoming connections
#         print(f"Accepting socket connections on {host}:{port}; use ctrl-C to stop")
#         while True:
#             try:
#                 # Use select to wait for an incoming connection so KeyboardInterrupt
#                 # works on Windows.
#                 rlist, _, _ = select.select([sock], [], [], 1)
#                 if rlist:
#                     sock_client, addr = sock.accept()
#                     print(f"Handling connection on {addr}")
#                     self._handle_client(sock_client)
#             except OSError:
#                 # Ignore a failed accept
#                 continue
#             except KeyboardInterrupt:
#                 print("Closing server")
#                 break
#         sock.close()

#     def _handle_client(self, sock, timeout_seconds=30):
#         sock.setblocking(False)
#         self.serial_sock_rx.setblocking(False)
#         # ADDED: (mvd) From Radiata - Drain receiver
#         while True:
#             rlist, _, _ = select.select([self.serial_sock_rx], [], [], 0)
#             if not rlist:
#                 break
#             self.serial_sock_rx.recv(BUF_SIZE)
#         try:
#             # ADDED: (mvd) From Radiata - Timer for timeout
#             t_last_data = time.time()
#             # ADDED: (mvd) From Radiata - rlist_wait?
#             rlist_wait = [sock, self.serial_sock_rx]
#             while rlist_wait and time.time() - t_last_data < timeout_seconds:
#                 print(time.time() - t_last_data)
#                 # Use select and use it only with sockets so it works on Windows.
#                 rlist, _, xlist = select.select(rlist_wait, [], [], 1)
#                 if xlist:
#                     print("Connection failed")
#                     return
#                 for s in rlist:
#                     t_last_data = time.time()
#                     if s == sock:
#                         buf = sock.recv(BUF_SIZE)
#                         if len(buf) == 0:
#                             print(f"Connection closed by client")
#                             rlist_wait.remove(s)
#                         else:
#                             if TRACE_SERIAL_DATA:
#                                 print(f"SER_WR({len(buf)}:{buf})")
#                             self.serial.write(buf)
#                     elif s == self.serial_sock_rx:
#                         buf = self.serial_sock_rx.recv(BUF_SIZE)
#                         if TRACE_SERIAL_DATA:
#                             print(f"SER_RD({buf})")
#                         if eof := buf.endswith(b"\x04"):
#                             buf = buf[:-1]
#                         sock.sendall(buf)
#                         if eof:
#                             print(f"Connection closed by server")
#                             rlist_wait.remove(s)
#         finally:
#             sock.close()


async def handle_tcp(reader, writer):
    await reader.readline()


sock_writer = None


async def ser_to_stream(ser_reader):
    global sock_writer
    try:
        while True:
            chunk = await ser_reader.read(255)
            if chunk:
                if chunk[0] == b"\x0E":
                    try:
                        n = chunk[1].to_int(1)
                    except IndexError:
                        n = (await ser_reader.read(1)).to_int("big")

                    got = max(len(chunk) - 2, 0)
                    if (
                        sock_writer
                        and sock_writer.transport is not None
                        and not sock_writer.transport.is_closing()
                    ):
                        print(f"\n[ser] > [sock] {n} bytes")
                        if got:
                            sock_writer.write(chunk[2:])
                            n -= got
                        while n:
                            n -= sock_writer.write(await ser_reader.read(n))
                    else:
                        # Clear incoming data
                        await ser_reader.read(n - got)

                else:
                    # print(chunk.decode(), end="")
                    print(chunk)

            else:
                await asyncio.sleep(0.1)
    except BrokenPipeError:
        print(f"[ser] --closed--")
        pass


# def chunks(data, n):
#     """Yield successive n-sized chunks from lst."""
#     for i in range(0, len(data), n):
#         yield data[i : i + n]


async def stream_to_ser(sock_reader, ser_writer):
    try:
        while (
            ser_writer.transport is not None and not ser_writer.transport.is_closing()
        ):
            chunk = await sock_reader.read(255)
            if chunk:
                n = len(chunk)
                print(f"[sock] > [ser] {n} bytes")
                ser_writer.write(b"\x0E" + n.to_bytes(1, "big"))
                ser_writer.write(chunk)
            else:
                await asyncio.sleep(0.1)
    except BrokenPipeError:
        print(f"[sock] --closed--")
        pass


def tcp_handler(ser_reader, ser_writer):
    async def server_cb(tcp_reader, tcp_writer):
        # tcp_writer.transport._sock.setblocking(False)
        global sock_writer
        sock_writer = tcp_writer

        await asyncio.create_task(stream_to_ser(tcp_reader, ser_writer))

    return server_cb


async def main():
    cmd_parser = argparse.ArgumentParser(description="Run a serial-socket adaptor.")
    cmd_parser.add_argument(
        "-d",
        "--device",
        default=None,
        help="serial device to use, can be partial device name, eg COM1",
    )
    cmd_parser.add_argument(
        "-b",
        "--baud",
        default=115200,
        help="Serial baud rate",
    )
    cmd_parser.add_argument(
        "-i",
        "--host",
        default="0.0.0.0",
        help="Local host to listen on",
    )
    cmd_parser.add_argument(
        "-p",
        "--port",
        default=8000,
        help="Local socket to listen on",
    )
    args = cmd_parser.parse_args()

    device_name = find_device(args.device)
    print(f"Connecting to USB serial device {device_name}")

    # loop = asyncio.get_event_loop()

    # ser_transport, ser_protocol = await serial_asyncio.create_serial_connection(loop, SerialProtocol, device_name, baudrate=args.baud)
    ser_reader, ser_writer = await serial_asyncio.open_serial_connection(
        url=device_name, baudrate=args.baud, timeout=REQUEST_TIMEOUT
    )

    print(f"Listening on http://{args.host}:{args.port}")
    # tcp_reader, tcp_writer = await asyncio.open_connection(args.host, args.port)
    srv = await asyncio.start_server(
        tcp_handler(ser_reader, ser_writer), args.host, args.port
    )

    await asyncio.gather(
        asyncio.create_task(ser_to_stream(ser_reader)),
        srv.serve_forever(),
    )

    # serial_port = ser_writer.transport
    # serial_port.set_timeout

    # tcp_transport, tcp_protocol = await loop.create_connection(SocketProtocol, args.host, args.port)

    # tcp_protocol.ser_writer = ser_transport
    # ser_protocol = tcp_transport

    # adaptor = SocketSerialAdaptor()
    # adaptor.open_serial(device_name)
    # adaptor.serve("0.0.0.0", 8000)


if __name__ == "__main__":
    asyncio.run(main())
