# SPDX-License-Identifier: MIT
# Copyright (c) 2023 MicroPython Team

"""
USB MTP (Media Transfer Protocol) device class implementation.

Based on the USB MTP Class specification version 1.1.
"""

import time
import struct
import micropython
import os
import io

from usb.device import Interface, Descriptor, Buffer
import usb.device

# MTP Class-specific constants
# MTP Class code (Miscellaneous with Interface Association Descriptor)
MTP_CLASS = 0x06
MTP_SUBCLASS = 0x01      # MTP Subclass
MTP_PROTOCOL = 0x01      # MTP Protocol

# MTP Operation Codes
MTP_OP_GET_DEVICE_INFO = 0x1001
MTP_OP_OPEN_SESSION = 0x1002
MTP_OP_CLOSE_SESSION = 0x1003
MTP_OP_GET_STORAGE_IDS = 0x1004
MTP_OP_GET_STORAGE_INFO = 0x1005
MTP_OP_GET_NUM_OBJECTS = 0x1006
MTP_OP_GET_OBJECT_HANDLES = 0x1007
MTP_OP_GET_OBJECT_INFO = 0x1008
MTP_OP_GET_OBJECT = 0x1009
MTP_OP_DELETE_OBJECT = 0x100A
MTP_OP_SEND_OBJECT_INFO = 0x100C
MTP_OP_SEND_OBJECT = 0x100D
MTP_OP_GET_PARTIAL_OBJECT = 0x101B

# MTP Response Codes
MTP_RESP_OK = 0x2001
MTP_RESP_SESSION_NOT_OPEN = 0x2003
MTP_RESP_INVALID_TRANSACTION_ID = 0x2004
MTP_RESP_OPERATION_NOT_SUPPORTED = 0x2005
MTP_RESP_PARAMETER_NOT_SUPPORTED = 0x2006
MTP_RESP_INCOMPLETE_TRANSFER = 0x2007
MTP_RESP_INVALID_STORAGE_ID = 0x2008
MTP_RESP_INVALID_OBJECT_HANDLE = 0x2009
MTP_RESP_STORE_FULL = 0x200C
MTP_RESP_STORE_READ_ONLY = 0x200E
MTP_RESP_GENERAL_ERROR = 0x2002

# MTP Container Types
MTP_CONTAINER_TYPE_COMMAND = 1
MTP_CONTAINER_TYPE_DATA = 2
MTP_CONTAINER_TYPE_RESPONSE = 3
MTP_CONTAINER_TYPE_EVENT = 4

# MTP Object Format Codes
MTP_FMT_UNDEFINED = 0x3000
MTP_FMT_ASSOCIATION = 0x3001  # Directory
MTP_FMT_TEXT = 0x3004         # Text file
MTP_FMT_BINARY = 0x3004       # Binary file (same as text for simplicity)

# Default EP sizes
MTP_BULK_EP_SIZE = 64
MTP_INTERRUPT_EP_SIZE = 8

# Maximum buffer sizes
MTP_MAX_PACKET_SIZE = 512   # Maximum bulk transfer size
MTP_MAX_FILENAME_LEN = 255  # Maximum filename length

# Helper functions for integer to bytes conversion
def u16_to_bytes(value):
    return struct.pack("<H", value)

def u32_to_bytes(value):
    return struct.pack("<I", value)

def string_to_utf16(s):
    utf16_bytes = s.encode('utf-16-le')
    # Add length including null terminator, as required by MTP
    length_bytes = struct.pack("<B", (len(utf16_bytes) // 2) + 1)  
    # Add null terminator (two bytes for UTF-16)
    return length_bytes + utf16_bytes + b'\x00\x00'

class MTPInterface(Interface):
    """MTP Interface for USB Media Transfer Protocol."""

    def __init__(self, root_dir="/"):
        """Initialize the MTP interface.

        Args:
            root_dir: Root directory to expose via MTP (default: "/")
        """
        super().__init__()
        self._root_dir = root_dir
        self._bulk_in_ep = None
        self._bulk_out_ep = None
        self._intr_ep = None
        self._bulk_in_buf = None
        self._bulk_out_buf = None
        self._rx_buf = None
        self._tx_buf = None
        self._rx_packet = None
        self._container_buf = bytearray(MTP_MAX_PACKET_SIZE)
        self._container_mview = memoryview(self._container_buf)
        
        # Session state
        self._session_open = False
        self._session_id = 0
        self._transaction_id = 0
        self._current_handle = 0
        self._storage_id = 0x00010001  # Fixed storage ID
        
        # Object handle mapping (handle -> path)
        self._handles = {}
        self._next_handle = 1
        
        # Transfer state
        self._transfer_active = False
        self._transfer_offset = 0
        self._transfer_length = 0
        self._transfer_file = None

    def init(self, packet_size=MTP_MAX_PACKET_SIZE):
        """Initialize the MTP interface with the given configuration.

        Args:
            packet_size: Maximum packet size for bulk transfers (default: 512)
        """
        # Create buffers
        self._bulk_in_buf = Buffer(packet_size * 2)  # Double buffering
        self._bulk_out_buf = Buffer(packet_size * 2)
        self._rx_buf = bytearray(packet_size)
        self._rx_packet = memoryview(self._rx_buf)
        self._tx_buf = bytearray(packet_size)
        
        # Set internal state
        self._transaction_id = 0
        self._session_open = False
        self._handles = {}
        self._next_handle = 1
        self._generate_object_handles()

    def _generate_object_handles(self):
        """Generate initial object handles for the filesystem."""
        self._handles = {}
        self._next_handle = 1
        
        # Add root directory as handle 1
        self._handles[1] = {"path": self._root_dir, "is_dir": True}
        self._next_handle = 2
        
        # Recursively add files and directories
        self._add_dir_entries(self._root_dir, 1)

    def _add_dir_entries(self, dir_path, parent_handle):
        """Add directory entries recursively.
        
        Args:
            dir_path: Path to the directory
            parent_handle: Handle of the parent directory
        """
        try:
            for entry in os.listdir(dir_path):
                path = dir_path
                if not path.endswith('/'):
                    path += '/'
                path += entry
                
                # Check if it's a directory
                try:
                    is_dir = os.stat(path)[0] & 0o170000 == 0o040000
                except OSError:
                    is_dir = False
                
                # Add to handles dictionary
                handle = self._next_handle
                self._handles[handle] = {"path": path, "is_dir": is_dir, "parent": parent_handle}
                self._next_handle += 1
                
                # Recursively add entries for subdirectories
                if is_dir:
                    self._add_dir_entries(path, handle)
        except OSError:
            pass  # Skip if directory cannot be accessed

    def get_object_info(self, handle):
        """Get information about an object by handle.
        
        Args:
            handle: Object handle
            
        Returns:
            Dict with object information or None if handle is invalid
        """
        if handle not in self._handles:
            return None
        
        obj = self._handles[handle]
        path = obj["path"]
        is_dir = obj["is_dir"]
        
        try:
            if is_dir:
                size = 0
                fmt = MTP_FMT_ASSOCIATION
            else:
                stat = os.stat(path)
                size = stat[6]  # st_size
                fmt = MTP_FMT_BINARY
            
            name = path.split('/')[-1]
            if name == '':
                name = '/'  # Root directory
            
            return {
                "StorageID": self._storage_id,
                "ObjectFormat": fmt,
                "ParentObject": obj.get("parent", 0),
                "Filename": name,
                "Size": size,
                "Handle": handle,
            }
        except OSError:
            return None

    def desc_cfg(self, desc):
        """Build configuration descriptor.
        
        Args:
            desc: Descriptor builder object
        """
        # Calculate total descriptor length
        len_itf = 9   # Interface descriptor
        len_eps = 7 * 3  # 3 endpoint descriptors
        len_itf_assoc = 8  # Interface Association Descriptor
        len_func = 5  # Functional descriptor (minimal)
        
        total_len = len_itf + len_eps + len_itf_assoc + len_func
        
        # Reserve space
        itf_idx = desc.reserve(total_len)
        self._itf_num = itf_idx
        
        # Build descriptor
        # Interface Association Descriptor
        desc.interface_assoc(
            itf_idx, 1, MTP_CLASS, MTP_SUBCLASS, MTP_PROTOCOL, "MTP"
        )
        
        # Interface descriptor
        desc.interface(
            itf_idx, 0, 3, MTP_CLASS, MTP_SUBCLASS, MTP_PROTOCOL, "MTP"
        )
        
        # Minimal functional descriptor
        desc.add(b"\x05\x24\x00\x01\x00")  # Minimal class-specific descriptor
        
        # Endpoint descriptors
        # Bulk OUT endpoint
        self._bulk_out_ep = usb.device.get().alloc_ep(1, usb.device.EP_TYPE_BULK, MTP_BULK_EP_SIZE)
        desc.endpoint(self._bulk_out_ep, usb.device.EP_TYPE_BULK, MTP_BULK_EP_SIZE)
        
        # Bulk IN endpoint
        self._bulk_in_ep = usb.device.get().alloc_ep(1, usb.device.EP_TYPE_BULK, MTP_BULK_EP_SIZE)
        desc.endpoint(self._bulk_in_ep | 0x80, usb.device.EP_TYPE_BULK, MTP_BULK_EP_SIZE)
        
        # Interrupt IN endpoint for events
        self._intr_ep = usb.device.get().alloc_ep(1, usb.device.EP_TYPE_INTERRUPT, MTP_INTERRUPT_EP_SIZE)
        desc.endpoint(self._intr_ep | 0x80, usb.device.EP_TYPE_INTERRUPT, MTP_INTERRUPT_EP_SIZE, 10)  # 10ms interval

    def control_req(self, req, *args, **kwargs):
        """Handle class-specific control requests.
        
        Args:
            req: USB_SETUP_DESCRIPTOR setup packet
            
        Returns:
            True if request was handled, False otherwise
        """
        # MTP doesn't use many control requests, most operations are done via bulk transfers
        return False

    def num_itfs(self):
        """Return number of interfaces."""
        return 1

    def num_eps(self):
        """Return number of endpoints."""
        return 3  # 2 bulk + 1 interrupt

    def on_reset(self):
        """Handle USB reset event."""
        self._session_open = False
        self._transfer_active = False
        if self._transfer_file:
            try:
                self._transfer_file.close()
            except:
                pass
            self._transfer_file = None

    def on_open(self):
        """Handle USB configuration event."""
        # Submit initial OUT transfer to receive commands
        self._submit_out_transfer()

    def is_open(self):
        """Check if the device is configured and open."""
        return usb.device.get().configured() and not self._session_open

    def _submit_out_transfer(self):
        """Submit an OUT transfer to receive data."""
        if not usb.device.get().configured():
            return
        usb.device.get().submit_xfer(
            self._bulk_out_ep, self._rx_packet, self._on_data_received
        )

    def _on_data_received(self, ep, data, status):
        """Handle received data from the USB host.
        
        Args:
            ep: Endpoint number
            data: Received data
            status: Transfer status
        """
        if status == usb.device.XFER_COMPLETED and len(data) > 0:
            # Process the received data
            self._process_container(data)
        
        # Submit a new transfer
        self._submit_out_transfer()

    def _send_data(self, data):
        """Send data to the host.
        
        Args:
            data: Data to send
        """
        if not usb.device.get().configured():
            return
        
        # Copy data to the transmit buffer
        tx_view = memoryview(self._tx_buf)
        length = min(len(data), len(tx_view))
        tx_view[:length] = data[:length]
        
        # Submit the transfer
        usb.device.get().submit_xfer(
            self._bulk_in_ep | 0x80, tx_view[:length], self._on_data_sent
        )

    def _on_data_sent(self, ep, data, status):
        """Handle completion of data transmission.
        
        Args:
            ep: Endpoint number
            data: Sent data
            status: Transfer status
        """
        pass  # Could be used for flow control

    def _process_container(self, data):
        """Process an MTP container.
        
        Args:
            data: Container data
        """
        if len(data) < 12:
            return  # Invalid container
        
        # Parse container header
        length = struct.unpack("<I", data[0:4])[0]
        container_type = struct.unpack("<H", data[4:6])[0]
        code = struct.unpack("<H", data[6:8])[0]
        transaction_id = struct.unpack("<I", data[8:12])[0]
        
        # Handle by container type
        if container_type == MTP_CONTAINER_TYPE_COMMAND:
            self._handle_command(code, transaction_id, data[12:length])
        elif container_type == MTP_CONTAINER_TYPE_DATA:
            self._handle_data(code, transaction_id, data[12:length])

    def _handle_command(self, op_code, transaction_id, params):
        """Handle an MTP command container.
        
        Args:
            op_code: MTP operation code
            transaction_id: Transaction ID
            params: Command parameters
        """
        self._transaction_id = transaction_id
        
        # Process command based on operation code
        if op_code == MTP_OP_GET_DEVICE_INFO:
            self._cmd_get_device_info()
        elif op_code == MTP_OP_OPEN_SESSION:
            self._cmd_open_session(params)
        elif op_code == MTP_OP_CLOSE_SESSION:
            self._cmd_close_session()
        elif op_code == MTP_OP_GET_STORAGE_IDS:
            self._cmd_get_storage_ids()
        elif op_code == MTP_OP_GET_STORAGE_INFO:
            self._cmd_get_storage_info(params)
        elif op_code == MTP_OP_GET_NUM_OBJECTS:
            self._cmd_get_num_objects(params)
        elif op_code == MTP_OP_GET_OBJECT_HANDLES:
            self._cmd_get_object_handles(params)
        elif op_code == MTP_OP_GET_OBJECT_INFO:
            self._cmd_get_object_info(params)
        elif op_code == MTP_OP_GET_OBJECT:
            self._cmd_get_object(params)
        elif op_code == MTP_OP_DELETE_OBJECT:
            self._cmd_delete_object(params)
        elif op_code == MTP_OP_SEND_OBJECT_INFO:
            self._cmd_send_object_info(params)
        elif op_code == MTP_OP_SEND_OBJECT:
            self._cmd_send_object()
        elif op_code == MTP_OP_GET_PARTIAL_OBJECT:
            self._cmd_get_partial_object(params)
        else:
            # Operation not supported
            self._send_response(MTP_RESP_OPERATION_NOT_SUPPORTED)

    def _handle_data(self, op_code, transaction_id, data):
        """Handle an MTP data container.
        
        Args:
            op_code: MTP operation code
            transaction_id: Transaction ID
            data: Container data
        """
        # Currently, the only operation that receives data is SEND_OBJECT
        if op_code == MTP_OP_SEND_OBJECT and self._transfer_active and self._transfer_file:
            try:
                self._transfer_file.write(data)
                self._transfer_offset += len(data)
                
                # Check if this is the last packet
                if self._transfer_offset >= self._transfer_length:
                    self._transfer_file.close()
                    self._transfer_file = None
                    self._transfer_active = False
                    self._send_response(MTP_RESP_OK)
            except OSError:
                if self._transfer_file:
                    self._transfer_file.close()
                self._transfer_file = None
                self._transfer_active = False
                self._send_response(MTP_RESP_GENERAL_ERROR)
        else:
            # Unexpected data phase
            self._send_response(MTP_RESP_GENERAL_ERROR)

    def _send_response(self, resp_code, params=None):
        """Send an MTP response container.
        
        Args:
            resp_code: Response code
            params: Optional parameters (list of integers)
        """
        # Calculate container size
        param_len = 0
        if params:
            param_len = len(params) * 4
        
        container_len = 12 + param_len  # Header + parameters
        
        # Build container header
        struct.pack_into("<I", self._container_buf, 0, container_len)
        struct.pack_into("<H", self._container_buf, 4, MTP_CONTAINER_TYPE_RESPONSE)
        struct.pack_into("<H", self._container_buf, 6, resp_code)
        struct.pack_into("<I", self._container_buf, 8, self._transaction_id)
        
        # Add parameters if any
        if params:
            offset = 12
            for param in params:
                struct.pack_into("<I", self._container_buf, offset, param)
                offset += 4
        
        # Send the response
        self._send_data(self._container_mview[:container_len])

    def _send_data_container(self, op_code, data, offset=0, length=None):
        """Send an MTP data container.
        
        Args:
            op_code: Operation code
            data: Data to send
            offset: Offset in data
            length: Length of data to send (or None for all)
        """
        if length is None:
            length = len(data) - offset
        
        container_len = 12 + length  # Header + data
        
        # Build container header
        struct.pack_into("<I", self._container_buf, 0, container_len)
        struct.pack_into("<H", self._container_buf, 4, MTP_CONTAINER_TYPE_DATA)
        struct.pack_into("<H", self._container_buf, 6, op_code)
        struct.pack_into("<I", self._container_buf, 8, self._transaction_id)
        
        # Add data
        if isinstance(data, (bytes, bytearray)):
            self._container_mview[12:12+length] = data[offset:offset+length]
        else:
            # Handle other data sources (like files)
            data_view = memoryview(self._container_mview)[12:12+length]
            # Read the data in chunks if needed
            pos = 0
            while pos < length:
                chunk = data.read(min(1024, length - pos))
                if not chunk:
                    break
                data_view[pos:pos+len(chunk)] = chunk
                pos += len(chunk)
        
        # Send the container
        self._send_data(self._container_mview[:container_len])

    def _cmd_get_device_info(self):
        """Handle GET_DEVICE_INFO operation."""
        # Build device info dataset
        data = bytearray(256)  # Pre-allocate buffer
        pos = 0
        
        # Standard version (1.1.0)
        pos += struct.pack_into("<H", data, pos, 0x0110)[0]
        # MTP vendor extension ID (none)
        pos += struct.pack_into("<I", data, pos, 0x00000000)[0]
        # MTP version (1.0.0)
        pos += struct.pack_into("<H", data, pos, 0x0100)[0]
        # MTP extensions (none)
        pos += struct.pack_into("<B", data, pos, 0)[0]
        
        # Functional mode (standard)
        pos += struct.pack_into("<H", data, pos, 0x0000)[0]
        
        # Supported operations array
        operations = [
            MTP_OP_GET_DEVICE_INFO,
            MTP_OP_OPEN_SESSION,
            MTP_OP_CLOSE_SESSION,
            MTP_OP_GET_STORAGE_IDS,
            MTP_OP_GET_STORAGE_INFO,
            MTP_OP_GET_NUM_OBJECTS,
            MTP_OP_GET_OBJECT_HANDLES,
            MTP_OP_GET_OBJECT_INFO,
            MTP_OP_GET_OBJECT,
            MTP_OP_DELETE_OBJECT,
            MTP_OP_SEND_OBJECT_INFO,
            MTP_OP_SEND_OBJECT,
            MTP_OP_GET_PARTIAL_OBJECT,
        ]
        pos += struct.pack_into("<I", data, pos, len(operations))[0]
        for op in operations:
            pos += struct.pack_into("<H", data, pos, op)[0]
        
        # Events array (none for simplicity)
        pos += struct.pack_into("<I", data, pos, 0)[0]
        
        # Device properties array (none for simplicity)
        pos += struct.pack_into("<I", data, pos, 0)[0]
        
        # Capture formats array (none)
        pos += struct.pack_into("<I", data, pos, 0)[0]
        
        # Playback formats array
        formats = [MTP_FMT_ASSOCIATION, MTP_FMT_BINARY, MTP_FMT_TEXT]
        pos += struct.pack_into("<I", data, pos, len(formats))[0]
        for fmt in formats:
            pos += struct.pack_into("<H", data, pos, fmt)[0]
        
        # Manufacturer
        manufacturer = string_to_utf16("MicroPython")
        data[pos:pos+len(manufacturer)] = manufacturer
        pos += len(manufacturer)
        
        # Model
        model = string_to_utf16("MTP Device")
        data[pos:pos+len(model)] = model
        pos += len(model)
        
        # Device version
        version = string_to_utf16("1.0")
        data[pos:pos+len(version)] = version
        pos += len(version)
        
        # Serial number
        serial = string_to_utf16("12345678")
        data[pos:pos+len(serial)] = serial
        pos += len(serial)
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_DEVICE_INFO, data[:pos])
        
        # Send the response
        self._send_response(MTP_RESP_OK)

    def _cmd_open_session(self, params):
        """Handle OPEN_SESSION operation.
        
        Args:
            params: Command parameters
        """
        if len(params) < 4:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        session_id = struct.unpack("<I", params[0:4])[0]
        
        if session_id == 0:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
        elif self._session_open:
            # Already open, send current session ID
            self._send_response(MTP_RESP_SESSION_NOT_OPEN, [self._session_id])
        else:
            # Open new session
            self._session_open = True
            self._session_id = session_id
            
            # Regenerate object handles
            self._generate_object_handles()
            
            self._send_response(MTP_RESP_OK)

    def _cmd_close_session(self):
        """Handle CLOSE_SESSION operation."""
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
        else:
            self._session_open = False
            self._session_id = 0
            self._send_response(MTP_RESP_OK)

    def _cmd_get_storage_ids(self):
        """Handle GET_STORAGE_IDS operation."""
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        # Build storage IDs array (only one storage)
        data = bytearray(8)
        struct.pack_into("<I", data, 0, 1)  # Count
        struct.pack_into("<I", data, 4, self._storage_id)  # Storage ID
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_STORAGE_IDS, data)
        
        # Send the response
        self._send_response(MTP_RESP_OK)

    def _cmd_get_storage_info(self, params):
        """Handle GET_STORAGE_INFO operation.
        
        Args:
            params: Command parameters
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        if len(params) < 4:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        storage_id = struct.unpack("<I", params[0:4])[0]
        
        if storage_id != self._storage_id:
            self._send_response(MTP_RESP_INVALID_STORAGE_ID)
            return
        
        # Build storage info dataset
        data = bytearray(128)
        pos = 0
        
        # Storage type (fixed)
        pos += struct.pack_into("<H", data, pos, 0x0001)[0]
        # Filesystem type (generic hierarchical)
        pos += struct.pack_into("<H", data, pos, 0x0002)[0]
        # Access capability (read-write)
        pos += struct.pack_into("<H", data, pos, 0x0000)[0]
        
        # Max capacity (4GB for simplicity)
        pos += struct.pack_into("<Q", data, pos, 4 * 1024 * 1024 * 1024)[0]
        # Free space (1GB for simplicity)
        pos += struct.pack_into("<Q", data, pos, 1 * 1024 * 1024 * 1024)[0]
        # Free objects (arbitrary)
        pos += struct.pack_into("<I", data, pos, 0xFFFFFFFF)[0]
        
        # Storage description
        description = string_to_utf16("MicroPython Storage")
        data[pos:pos+len(description)] = description
        pos += len(description)
        
        # Volume identifier
        volume_id = string_to_utf16("MPYSTORAGE")
        data[pos:pos+len(volume_id)] = volume_id
        pos += len(volume_id)
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_STORAGE_INFO, data[:pos])
        
        # Send the response
        self._send_response(MTP_RESP_OK)

    def _cmd_get_num_objects(self, params):
        """Handle GET_NUM_OBJECTS operation.
        
        Args:
            params: Command parameters
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        if len(params) < 4:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        storage_id = struct.unpack("<I", params[0:4])[0]
        
        if storage_id != self._storage_id and storage_id != 0xFFFFFFFF:
            self._send_response(MTP_RESP_INVALID_STORAGE_ID)
            return
        
        # Get parent object handle
        parent_handle = 0xFFFFFFFF  # All objects
        if len(params) >= 8:
            parent_handle = struct.unpack("<I", params[4:8])[0]
        
        # Count objects
        count = 0
        if parent_handle == 0xFFFFFFFF:
            # All objects
            count = len(self._handles)
        else:
            # Objects with specific parent
            for handle, obj in self._handles.items():
                if obj.get("parent", 0) == parent_handle:
                    count += 1
        
        # Send the response with object count
        self._send_response(MTP_RESP_OK, [count])

    def _cmd_get_object_handles(self, params):
        """Handle GET_OBJECT_HANDLES operation.
        
        Args:
            params: Command parameters
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        if len(params) < 12:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        storage_id = struct.unpack("<I", params[0:4])[0]
        format_code = struct.unpack("<I", params[4:8])[0]
        parent_handle = struct.unpack("<I", params[8:12])[0]
        
        if storage_id != self._storage_id and storage_id != 0xFFFFFFFF:
            self._send_response(MTP_RESP_INVALID_STORAGE_ID)
            return
        
        # Get matching handles
        handles = []
        for handle, obj in self._handles.items():
            # Check storage ID (always matches as we have only one)
            
            # Check parent handle
            if parent_handle != 0xFFFFFFFF and obj.get("parent", 0) != parent_handle:
                continue
            
            # Check format code
            if format_code != 0:
                obj_info = self.get_object_info(handle)
                if not obj_info or obj_info["ObjectFormat"] != format_code:
                    continue
            
            handles.append(handle)
        
        # Build handles array
        data_len = 4 + len(handles) * 4
        data = bytearray(data_len)
        
        # Number of handles
        struct.pack_into("<I", data, 0, len(handles))
        
        # Handle values
        for i, handle in enumerate(handles):
            struct.pack_into("<I", data, 4 + i * 4, handle)
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_OBJECT_HANDLES, data)
        
        # Send the response
        self._send_response(MTP_RESP_OK)

    def _cmd_get_object_info(self, params):
        """Handle GET_OBJECT_INFO operation.
        
        Args:
            params: Command parameters
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        if len(params) < 4:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        handle = struct.unpack("<I", params[0:4])[0]
        
        obj_info = self.get_object_info(handle)
        if not obj_info:
            self._send_response(MTP_RESP_INVALID_OBJECT_HANDLE)
            return
        
        # Build object info dataset
        data = bytearray(256)
        pos = 0
        
        # Storage ID
        pos += struct.pack_into("<I", data, pos, obj_info["StorageID"])[0]
        # Object format
        pos += struct.pack_into("<H", data, pos, obj_info["ObjectFormat"])[0]
        # Protection status (not protected)
        pos += struct.pack_into("<H", data, pos, 0x0000)[0]
        # Object size
        pos += struct.pack_into("<I", data, pos, obj_info["Size"])[0]
        
        # Thumbnail format (none)
        pos += struct.pack_into("<H", data, pos, 0x0000)[0]
        # Thumbnail size
        pos += struct.pack_into("<I", data, pos, 0)[0]
        # Thumbnail width
        pos += struct.pack_into("<I", data, pos, 0)[0]
        # Thumbnail height
        pos += struct.pack_into("<I", data, pos, 0)[0]
        
        # Image width (not applicable)
        pos += struct.pack_into("<I", data, pos, 0)[0]
        # Image height
        pos += struct.pack_into("<I", data, pos, 0)[0]
        # Image depth
        pos += struct.pack_into("<I", data, pos, 0)[0]
        
        # Parent object
        pos += struct.pack_into("<I", data, pos, obj_info["ParentObject"])[0]
        
        # Association type (if folder)
        if obj_info["ObjectFormat"] == MTP_FMT_ASSOCIATION:
            pos += struct.pack_into("<H", data, pos, 0x0001)[0]  # Generic folder
        else:
            pos += struct.pack_into("<H", data, pos, 0x0000)[0]  # Undefined
        
        # Association description
        pos += struct.pack_into("<I", data, pos, 0)[0]
        
        # Sequence number (not used)
        pos += struct.pack_into("<I", data, pos, 0)[0]
        
        # Filename
        filename = string_to_utf16(obj_info["Filename"])
        data[pos:pos+len(filename)] = filename
        pos += len(filename)
        
        # Date created (empty string)
        data[pos] = 0
        pos += 1
        
        # Date modified (empty string)
        data[pos] = 0
        pos += 1
        
        # Keywords (empty string)
        data[pos] = 0
        pos += 1
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_OBJECT_INFO, data[:pos])
        
        # Send the response
        self._send_response(MTP_RESP_OK)

    def _cmd_get_object(self, params):
        """Handle GET_OBJECT operation.
        
        Args:
            params: Command parameters
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        if len(params) < 4:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        handle = struct.unpack("<I", params[0:4])[0]
        
        if handle not in self._handles:
            self._send_response(MTP_RESP_INVALID_OBJECT_HANDLE)
            return
        
        obj = self._handles[handle]
        path = obj["path"]
        is_dir = obj["is_dir"]
        
        if is_dir:
            # Directory object - send empty data
            self._send_data_container(MTP_OP_GET_OBJECT, b"")
            self._send_response(MTP_RESP_OK)
            return
        
        try:
            # Open file and send its contents
            with open(path, "rb") as f:
                # Read and send file data in chunks
                file_size = 0
                chunk_size = MTP_MAX_PACKET_SIZE - 12  # Account for header
                
                # Try to get file size
                try:
                    file_size = os.stat(path)[6]  # st_size
                except OSError:
                    # Read whole file if size can't be determined
                    data = f.read()
                    self._send_data_container(MTP_OP_GET_OBJECT, data)
                    self._send_response(MTP_RESP_OK)
                    return
                
                # Create container header
                header = bytearray(12)
                header_len = 12 + file_size
                struct.pack_into("<I", header, 0, header_len)
                struct.pack_into("<H", header, 4, MTP_CONTAINER_TYPE_DATA)
                struct.pack_into("<H", header, 6, MTP_OP_GET_OBJECT)
                struct.pack_into("<I", header, 8, self._transaction_id)
                
                # Send header
                self._send_data(header)
                
                # Send file content in chunks
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    self._send_data(chunk)
                
                # Send response
                self._send_response(MTP_RESP_OK)
        except OSError:
            self._send_response(MTP_RESP_GENERAL_ERROR)

    def _cmd_delete_object(self, params):
        """Handle DELETE_OBJECT operation.
        
        Args:
            params: Command parameters
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        if len(params) < 4:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        handle = struct.unpack("<I", params[0:4])[0]
        
        if handle not in self._handles:
            self._send_response(MTP_RESP_INVALID_OBJECT_HANDLE)
            return
        
        obj = self._handles[handle]
        path = obj["path"]
        is_dir = obj["is_dir"]
        
        try:
            if is_dir:
                # Remove directory
                os.rmdir(path)
            else:
                # Remove file
                os.remove(path)
            
            # Remove children if it's a directory
            children = []
            for h, o in self._handles.items():
                if o.get("parent", 0) == handle:
                    children.append(h)
            
            for child in children:
                del self._handles[child]
            
            # Remove the object
            del self._handles[handle]
            
            self._send_response(MTP_RESP_OK)
        except OSError:
            self._send_response(MTP_RESP_GENERAL_ERROR)

    def _cmd_send_object_info(self, params):
        """Handle SEND_OBJECT_INFO operation.
        
        Args:
            params: Command parameters containing parent handle
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        # Wait for data container with object info
        # This will be handled in the data handler
        
        # For now, store parent handle
        if len(params) >= 4:
            self._current_handle = struct.unpack("<I", params[0:4])[0]
        else:
            self._current_handle = 0
        
        # Handle in _handle_data
        # For now, respond with OK
        self._send_response(MTP_RESP_OK)

    def _cmd_send_object(self):
        """Handle SEND_OBJECT operation."""
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        # Data will be received in _handle_data
        # For now, respond with OK
        self._send_response(MTP_RESP_OK)

    def _cmd_get_partial_object(self, params):
        """Handle GET_PARTIAL_OBJECT operation.
        
        Args:
            params: Command parameters
        """
        if not self._session_open:
            self._send_response(MTP_RESP_SESSION_NOT_OPEN)
            return
        
        if len(params) < 12:
            self._send_response(MTP_RESP_PARAMETER_NOT_SUPPORTED)
            return
        
        handle = struct.unpack("<I", params[0:4])[0]
        offset = struct.unpack("<I", params[4:8])[0]
        size = struct.unpack("<I", params[8:12])[0]
        
        if handle not in self._handles:
            self._send_response(MTP_RESP_INVALID_OBJECT_HANDLE)
            return
        
        obj = self._handles[handle]
        path = obj["path"]
        is_dir = obj["is_dir"]
        
        if is_dir:
            # Directory object - send empty data
            self._send_data_container(MTP_OP_GET_PARTIAL_OBJECT, b"")
            self._send_response(MTP_RESP_OK, [0])  # Sent 0 bytes
            return
        
        try:
            # Open file and send partial contents
            with open(path, "rb") as f:
                # Seek to offset
                f.seek(offset)
                
                # Read requested data
                data = f.read(size)
                
                # Send data
                self._send_data_container(MTP_OP_GET_PARTIAL_OBJECT, data)
                
                # Send response with bytes sent
                self._send_response(MTP_RESP_OK, [len(data)])
        except OSError:
            self._send_response(MTP_RESP_GENERAL_ERROR)