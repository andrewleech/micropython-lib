# SPDX-License-Identifier: MIT
# Copyright (c) 2023 MicroPython Team

"""
USB MTP (Media Transfer Protocol) device class implementation.

Based on the USB MTP Class specification version 1.1.
"""

import time
import micropython
import os
import io
import uctypes

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

# MTP Container descriptor layout
MTP_CONTAINER_DESC = {
    "length": 0 | uctypes.UINT32,
    "type": 4 | uctypes.UINT16,
    "code": 6 | uctypes.UINT16,
    "transaction_id": 8 | uctypes.UINT32,
}

# MTP Parameter descriptor layout
MTP_PARAM_DESC = {
    "value": 0 | uctypes.UINT32,
}

# Device Info descriptor layout
DEVICE_INFO_DESC = {
    "standard_version": 0 | uctypes.UINT16,
    "vendor_ext_id": 2 | uctypes.UINT32,
    "mtp_version": 6 | uctypes.UINT16,
    "mtp_extensions": 8 | uctypes.UINT8,
    "functional_mode": 9 | uctypes.UINT16,
    "operations_supported_len": 11 | uctypes.UINT32,
    # Array of operations follows
}

# Storage Info descriptor layout
STORAGE_INFO_DESC = {
    "storage_type": 0 | uctypes.UINT16,
    "filesystem_type": 2 | uctypes.UINT16,
    "access_capability": 4 | uctypes.UINT16,
    "max_capacity": 6 | uctypes.UINT64,
    "free_space": 14 | uctypes.UINT64,
    "free_objects": 22 | uctypes.UINT32,
}

# Object Info descriptor layout
OBJECT_INFO_DESC = {
    "storage_id": 0 | uctypes.UINT32,
    "object_format": 4 | uctypes.UINT16,
    "protection_status": 6 | uctypes.UINT16,
    "object_size": 8 | uctypes.UINT32,
    "thumb_format": 12 | uctypes.UINT16,
    "thumb_size": 14 | uctypes.UINT32,
    "thumb_width": 18 | uctypes.UINT32,
    "thumb_height": 22 | uctypes.UINT32,
    "image_width": 26 | uctypes.UINT32,
    "image_height": 30 | uctypes.UINT32,
    "image_depth": 34 | uctypes.UINT32,
    "parent_object": 38 | uctypes.UINT32,
    "association_type": 42 | uctypes.UINT16,
    "association_desc": 44 | uctypes.UINT32,
    "sequence_number": 48 | uctypes.UINT32,
}

# MTP Storage IDs descriptor layout
STORAGE_IDS_DESC = {
    "count": 0 | uctypes.UINT32,
    # Storage IDs follow
}

# MTP Object Handles descriptor layout
OBJECT_HANDLES_DESC = {
    "count": 0 | uctypes.UINT32,
    # Handles follow
}

def string_to_utf16(s):
    """Convert ASCII string to UTF-16 with length prefix as required by MTP."""
    utf16_bytes = s.encode('utf-16-le')
    # Create result with length byte + string + null terminator
    result = bytearray(1 + len(utf16_bytes) + 2)
    # Add length including null terminator (in characters, not bytes)
    result[0] = (len(utf16_bytes) // 2) + 1
    # Copy string bytes
    for i in range(len(utf16_bytes)):
        result[1 + i] = utf16_bytes[i]
    # Null terminator is already initialized to 0
    return result

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
        self._itf_num = None
        
        # Create packet and container buffers
        self._container_buf = bytearray(MTP_MAX_PACKET_SIZE)
        self._container_mview = memoryview(self._container_buf)
        
        # Create uctypes struct for container header
        self._container = uctypes.struct(
            uctypes.addressof(self._container_buf), 
            MTP_CONTAINER_DESC, 
            uctypes.LITTLE_ENDIAN
        )
        
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
        
        # Initialize with default parameters
        self.init()

    def init(self, packet_size=MTP_MAX_PACKET_SIZE):
        """Initialize the MTP interface with the given configuration.

        Args:
            packet_size: Maximum packet size for bulk transfers (default: 512)
        """
        # Create buffers for data transfer
        self._rx_buf = bytearray(packet_size)
        self._rx_packet = memoryview(self._rx_buf)
        self._tx_buf = bytearray(packet_size)
        
        # Reset internal state
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

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        """Build configuration descriptor.
        
        Args:
            desc: Descriptor builder object
            itf_num: Interface number to use
            ep_num: First endpoint number to use
            strs: String descriptor array
        """
        # Store interface number
        self._itf_num = itf_num
        
        # Interface Association Descriptor for MTP
        desc.interface_assoc(itf_num, 1, MTP_CLASS, MTP_SUBCLASS, MTP_PROTOCOL)
        
        # Interface descriptor
        desc.interface(itf_num, 3, MTP_CLASS, MTP_SUBCLASS, MTP_PROTOCOL)
        
        # Class-specific functional descriptor
        desc.pack(
            "<BBBBB",
            5,  # bFunctionLength
            0x24,  # bDescriptorType - CS_INTERFACE
            0x00,  # bDescriptorSubtype
            0x01,  # bcdMTPVersion - 1.0 LSB
            0x00,  # bcdMTPVersion - 1.0 MSB
        )
        
        # Endpoint descriptors
        # Bulk OUT endpoint
        self._bulk_out_ep = ep_num
        desc.endpoint(self._bulk_out_ep, "bulk", MTP_BULK_EP_SIZE, 0)
        
        # Bulk IN endpoint
        self._bulk_in_ep = (ep_num + 1) | 0x80
        desc.endpoint(self._bulk_in_ep, "bulk", MTP_BULK_EP_SIZE, 0)
        
        # Interrupt IN endpoint for events
        self._intr_ep = (ep_num + 2) | 0x80
        desc.endpoint(self._intr_ep, "interrupt", MTP_INTERRUPT_EP_SIZE, 10)  # 10ms interval

    def on_interface_control_xfer(self, stage, request):
        """Handle class-specific interface control transfers.
        
        Args:
            stage: Stage of the control transfer
            request: The setup packet
            
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
        return super().is_open()

    def _submit_out_transfer(self):
        """Submit an OUT transfer to receive data."""
        if not super().is_open():
            return
        
        self.submit_xfer(self._bulk_out_ep, self._rx_packet, self._on_data_received)

    def _on_data_received(self, ep, res, num_bytes):
        """Handle received data from the USB host.
        
        Args:
            ep: Endpoint number
            res: Result code (0 for success)
            num_bytes: Number of bytes received
        """
        if res == 0 and num_bytes > 0:
            # Process the received data
            self._process_container(self._rx_packet[:num_bytes])
        
        # Submit a new transfer
        self._submit_out_transfer()

    def _send_data(self, data):
        """Send data to the host.
        
        Args:
            data: Data to send
        """
        if not super().is_open():
            return
        
        # Copy data to the transmit buffer
        tx_view = memoryview(self._tx_buf)
        length = min(len(data), len(tx_view))
        tx_view[:length] = data[:length]
        
        # Submit the transfer
        self.submit_xfer(self._bulk_in_ep, tx_view[:length], self._on_data_sent)

    def _on_data_sent(self, ep, res, num_bytes):
        """Handle completion of data transmission.
        
        Args:
            ep: Endpoint number
            res: Result code (0 for success)
            num_bytes: Number of bytes sent
        """
        pass  # Could be used for flow control

    def _process_container(self, data):
        """Process an MTP container.
        
        Args:
            data: Container data
        """
        if len(data) < 12:
            return  # Invalid container
        
        # Parse container header using uctypes
        # Create a temporary container descriptor
        container = uctypes.struct(
            uctypes.addressof(bytearray(data)), 
            MTP_CONTAINER_DESC, 
            uctypes.LITTLE_ENDIAN
        )
        
        length = container.length
        container_type = container.type
        code = container.code
        transaction_id = container.transaction_id
        
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
        
        # Build container header using uctypes
        self._container.length = container_len
        self._container.type = MTP_CONTAINER_TYPE_RESPONSE
        self._container.code = resp_code
        self._container.transaction_id = self._transaction_id
        
        # Add parameters if any
        if params:
            param_buf = memoryview(self._container_buf)[12:12+param_len]
            for i, param in enumerate(params):
                # Create a parameter struct for each 4-byte parameter
                param_struct = uctypes.struct(
                    uctypes.addressof(param_buf) + i * 4,
                    MTP_PARAM_DESC,
                    uctypes.LITTLE_ENDIAN
                )
                param_struct.value = param
        
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
        
        # Build container header using uctypes
        self._container.length = container_len
        self._container.type = MTP_CONTAINER_TYPE_DATA
        self._container.code = op_code
        self._container.transaction_id = self._transaction_id
        
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
        # Allocate buffer for device info
        buf = bytearray(256)
        
        # Create uctypes struct for device info
        dev_info = uctypes.struct(
            uctypes.addressof(buf),
            DEVICE_INFO_DESC,
            uctypes.LITTLE_ENDIAN
        )
        
        # Fill in device info fields
        dev_info.standard_version = 0x0110  # 1.1.0
        dev_info.vendor_ext_id = 0x00000000  # No extensions
        dev_info.mtp_version = 0x0100  # 1.0.0
        dev_info.mtp_extensions = 0  # No extensions
        dev_info.functional_mode = 0x0000  # Standard mode
        
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
        dev_info.operations_supported_len = len(operations)
        
        # Add operations to buffer
        pos = 15  # Start after fixed header (11 bytes) + operations_supported_len (4 bytes)
        for op in operations:
            buf[pos] = op & 0xFF
            buf[pos+1] = (op >> 8) & 0xFF
            pos += 2
        
        # Events array (none for simplicity)
        buf[pos] = 0
        buf[pos+1] = 0
        buf[pos+2] = 0
        buf[pos+3] = 0
        pos += 4
        
        # Device properties array (none for simplicity)
        buf[pos] = 0
        buf[pos+1] = 0
        buf[pos+2] = 0
        buf[pos+3] = 0
        pos += 4
        
        # Capture formats array (none)
        buf[pos] = 0
        buf[pos+1] = 0
        buf[pos+2] = 0
        buf[pos+3] = 0
        pos += 4
        
        # Playback formats array
        formats = [MTP_FMT_ASSOCIATION, MTP_FMT_BINARY, MTP_FMT_TEXT]
        # Format count
        buf[pos] = len(formats) & 0xFF
        buf[pos+1] = (len(formats) >> 8) & 0xFF
        buf[pos+2] = (len(formats) >> 16) & 0xFF
        buf[pos+3] = (len(formats) >> 24) & 0xFF
        pos += 4
        # Format codes
        for fmt in formats:
            buf[pos] = fmt & 0xFF
            buf[pos+1] = (fmt >> 8) & 0xFF
            pos += 2
        
        # Manufacturer
        manufacturer = string_to_utf16("MicroPython")
        buf[pos:pos+len(manufacturer)] = manufacturer
        pos += len(manufacturer)
        
        # Model
        model = string_to_utf16("MTP Device")
        buf[pos:pos+len(model)] = model
        pos += len(model)
        
        # Device version
        version = string_to_utf16("1.0")
        buf[pos:pos+len(version)] = version
        pos += len(version)
        
        # Serial number
        serial = string_to_utf16("12345678")
        buf[pos:pos+len(serial)] = serial
        pos += len(serial)
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_DEVICE_INFO, buf[:pos])
        
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
        
        # Create parameter struct
        param_struct = uctypes.struct(
            uctypes.addressof(bytearray(params[:4])),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        session_id = param_struct.value
        
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
        
        # Create storage IDs buffer
        buf = bytearray(8)
        
        # Create storage IDs struct
        storage_ids = uctypes.struct(
            uctypes.addressof(buf),
            STORAGE_IDS_DESC,
            uctypes.LITTLE_ENDIAN
        )
        
        # Set count to 1 (only one storage)
        storage_ids.count = 1
        
        # Set storage ID after the count field
        id_struct = uctypes.struct(
            uctypes.addressof(buf) + 4,
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        id_struct.value = self._storage_id
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_STORAGE_IDS, buf)
        
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
        
        # Create parameter struct
        param_struct = uctypes.struct(
            uctypes.addressof(bytearray(params[:4])),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        storage_id = param_struct.value
        
        if storage_id != self._storage_id:
            self._send_response(MTP_RESP_INVALID_STORAGE_ID)
            return
        
        # Create storage info buffer
        buf = bytearray(128)
        
        # Create storage info struct
        storage_info = uctypes.struct(
            uctypes.addressof(buf),
            STORAGE_INFO_DESC,
            uctypes.LITTLE_ENDIAN
        )
        
        # Fill in storage info fields
        storage_info.storage_type = 0x0001  # Fixed storage
        storage_info.filesystem_type = 0x0002  # Generic hierarchical
        storage_info.access_capability = 0x0000  # Read-write
        storage_info.max_capacity = 4 * 1024 * 1024 * 1024  # 4GB for simplicity
        storage_info.free_space = 1 * 1024 * 1024 * 1024  # 1GB for simplicity
        storage_info.free_objects = 0xFFFFFFFF  # Arbitrary value
        
        # Add strings after the fixed fields
        pos = 26  # Size of fixed storage info fields
        
        # Storage description
        description = string_to_utf16("MicroPython Storage")
        buf[pos:pos+len(description)] = description
        pos += len(description)
        
        # Volume identifier
        volume_id = string_to_utf16("MPYSTORAGE")
        buf[pos:pos+len(volume_id)] = volume_id
        pos += len(volume_id)
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_STORAGE_INFO, buf[:pos])
        
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
        
        # Create parameter struct for storage ID
        storage_id_struct = uctypes.struct(
            uctypes.addressof(bytearray(params[:4])),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        storage_id = storage_id_struct.value
        
        if storage_id != self._storage_id and storage_id != 0xFFFFFFFF:
            self._send_response(MTP_RESP_INVALID_STORAGE_ID)
            return
        
        # Get parent object handle
        parent_handle = 0xFFFFFFFF  # All objects
        if len(params) >= 8:
            parent_handle_struct = uctypes.struct(
                uctypes.addressof(bytearray(params[4:8])),
                MTP_PARAM_DESC,
                uctypes.LITTLE_ENDIAN
            )
            parent_handle = parent_handle_struct.value
        
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
        
        # Create parameter structs
        param_buf = bytearray(params[:12])
        storage_id_struct = uctypes.struct(
            uctypes.addressof(param_buf),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        format_code_struct = uctypes.struct(
            uctypes.addressof(param_buf) + 4,
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        parent_handle_struct = uctypes.struct(
            uctypes.addressof(param_buf) + 8,
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        
        storage_id = storage_id_struct.value
        format_code = format_code_struct.value
        parent_handle = parent_handle_struct.value
        
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
        
        # Create object handles buffer
        data_len = 4 + len(handles) * 4
        buf = bytearray(data_len)
        
        # Create object handles struct
        obj_handles = uctypes.struct(
            uctypes.addressof(buf),
            OBJECT_HANDLES_DESC,
            uctypes.LITTLE_ENDIAN
        )
        
        # Set count
        obj_handles.count = len(handles)
        
        # Add handles after the count field
        for i, handle in enumerate(handles):
            handle_struct = uctypes.struct(
                uctypes.addressof(buf) + 4 + i * 4,
                MTP_PARAM_DESC,
                uctypes.LITTLE_ENDIAN
            )
            handle_struct.value = handle
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_OBJECT_HANDLES, buf)
        
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
        
        # Create parameter struct for handle
        handle_struct = uctypes.struct(
            uctypes.addressof(bytearray(params[:4])),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        handle = handle_struct.value
        
        obj_info = self.get_object_info(handle)
        if not obj_info:
            self._send_response(MTP_RESP_INVALID_OBJECT_HANDLE)
            return
        
        # Create object info buffer
        buf = bytearray(256)
        
        # Create object info struct
        obj_info_struct = uctypes.struct(
            uctypes.addressof(buf),
            OBJECT_INFO_DESC,
            uctypes.LITTLE_ENDIAN
        )
        
        # Fill in object info fields
        obj_info_struct.storage_id = obj_info["StorageID"]
        obj_info_struct.object_format = obj_info["ObjectFormat"]
        obj_info_struct.protection_status = 0x0000  # Not protected
        obj_info_struct.object_size = obj_info["Size"]
        obj_info_struct.thumb_format = 0x0000  # No thumbnail
        obj_info_struct.thumb_size = 0
        obj_info_struct.thumb_width = 0
        obj_info_struct.thumb_height = 0
        obj_info_struct.image_width = 0  # Not applicable
        obj_info_struct.image_height = 0
        obj_info_struct.image_depth = 0
        obj_info_struct.parent_object = obj_info["ParentObject"]
        
        # Association type (if folder)
        if obj_info["ObjectFormat"] == MTP_FMT_ASSOCIATION:
            obj_info_struct.association_type = 0x0001  # Generic folder
        else:
            obj_info_struct.association_type = 0x0000  # Undefined
        
        obj_info_struct.association_desc = 0
        obj_info_struct.sequence_number = 0
        
        # Add filename after the fixed fields
        pos = 52  # Size of fixed object info fields
        
        # Filename
        filename = string_to_utf16(obj_info["Filename"])
        buf[pos:pos+len(filename)] = filename
        pos += len(filename)
        
        # Date created (empty string)
        buf[pos] = 0  # Empty string length
        pos += 1
        
        # Date modified (empty string)
        buf[pos] = 0  # Empty string length
        pos += 1
        
        # Keywords (empty string)
        buf[pos] = 0  # Empty string length
        pos += 1
        
        # Send the data container
        self._send_data_container(MTP_OP_GET_OBJECT_INFO, buf[:pos])
        
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
        
        # Create parameter struct for handle
        handle_struct = uctypes.struct(
            uctypes.addressof(bytearray(params[:4])),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        handle = handle_struct.value
        
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
                
                # Create container header with uctypes
                header = bytearray(12)
                container = uctypes.struct(
                    uctypes.addressof(header),
                    MTP_CONTAINER_DESC,
                    uctypes.LITTLE_ENDIAN
                )
                
                container.length = 12 + file_size
                container.type = MTP_CONTAINER_TYPE_DATA
                container.code = MTP_OP_GET_OBJECT
                container.transaction_id = self._transaction_id
                
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
        
        # Create parameter struct for handle
        handle_struct = uctypes.struct(
            uctypes.addressof(bytearray(params[:4])),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        handle = handle_struct.value
        
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
            # Create parameter struct for handle
            handle_struct = uctypes.struct(
                uctypes.addressof(bytearray(params[:4])),
                MTP_PARAM_DESC,
                uctypes.LITTLE_ENDIAN
            )
            self._current_handle = handle_struct.value
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
        
        # Create parameter structs
        param_buf = bytearray(params[:12])
        handle_struct = uctypes.struct(
            uctypes.addressof(param_buf),
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        offset_struct = uctypes.struct(
            uctypes.addressof(param_buf) + 4,
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        size_struct = uctypes.struct(
            uctypes.addressof(param_buf) + 8,
            MTP_PARAM_DESC,
            uctypes.LITTLE_ENDIAN
        )
        
        handle = handle_struct.value
        offset = offset_struct.value
        size = size_struct.value
        
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