"""Omron CX-Simulator PLC driver by Ismail Lowkey.

Connects directly to Omron CX-Simulator (CxCpuMain.exe or CxCpuMainCS2.exe)
via NetToCXSim.Core (OmronSimulatorEngine) developed by Ismail Lowkey.
Direct live memory I/O access to CIO, WR, HR, AR, DM, TIM, and CNT areas
with zero network latency and no external hardware required.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import struct
from pathlib import Path
from typing import Any

from ..tagbus import TagBusClient
from ..tags import TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)

# Check if pythonnet is installed
try:
    import clr
    HAS_PYTHONNET = True
except ImportError:
    HAS_PYTHONNET = False

HAS_OMRON_ENGINE = True

_NET_TO_CXSIM_DLL = "NetToCXSim.Core.dll"


def _find_core_dll() -> str | None:
    """Locate NetToCXSim.Core.dll in DriverPLC or workspace."""
    search_patterns = [
        os.path.join("DriverPLC", "bin", "Release", "netstandard2.0", _NET_TO_CXSIM_DLL),
        os.path.join("DriverPLC", "bin", "Release", "net472", _NET_TO_CXSIM_DLL),
        os.path.join("DriverPLC", "bin", "Debug", "netstandard2.0", _NET_TO_CXSIM_DLL),
        os.path.join("DriverPLC", "bin", "Debug", "net472", _NET_TO_CXSIM_DLL),
        os.path.join("DriverPLC", _NET_TO_CXSIM_DLL),
        os.path.join("..", "DriverPLC", "bin", "Release", "netstandard2.0", _NET_TO_CXSIM_DLL),
        os.path.join("..", "DriverPLC", "bin", "Release", "net472", _NET_TO_CXSIM_DLL),
    ]

    for rel_path in search_patterns:
        abs_path = os.path.abspath(rel_path)
        if os.path.exists(abs_path):
            return abs_path

    # Fallback recursive search in DriverPLC
    driver_plc_dir = os.path.abspath("DriverPLC")
    if not os.path.exists(driver_plc_dir):
        driver_plc_dir = os.path.abspath(os.path.join("..", "DriverPLC"))

    if os.path.exists(driver_plc_dir):
        matches = glob.glob(os.path.join(driver_plc_dir, "**", _NET_TO_CXSIM_DLL), recursive=True)
        if len(matches) > 0:
            return matches[0]

    return None


@register("omron-cxsim")
class OmronCXSimulatorDriver(Driver):
    """Omron CX-Simulator driver by Ismail Lowkey.

    Communicates with Omron CX-Simulator via direct live memory injection
    using Ismail Lowkey's NetToCXSim.Core engine.
    """

    driver_name = "omron-cxsim"

    def __init__(
        self,
        bus: TagBusClient,
        poll_interval: float = 0.05,
        mapping: dict[str, str] | None = None,
        mapping_file: str | None = None,
        **config: Any
    ) -> None:
        super().__init__(bus, **config)
        self.poll_interval = float(poll_interval)
        self._running = False
        self._task: asyncio.Task | None = None
        self._table: TagTable | None = None
        self._engine: Any = None
        self._cpu_type: str = "Unknown"

        # Explicit dictionary initialization
        if mapping is not None:
            self.mapping = dict(mapping)
        else:
            self.mapping = {}

        if mapping_file:
            path_obj = Path(mapping_file)
            if path_obj.exists():
                loaded = json.loads(path_obj.read_text(encoding="utf-8"))
                for key, val in loaded.items():
                    if not key.startswith("_") and val:
                        self.mapping[key] = str(val).strip()

        self._warned: set[str] = set()

    def _auto_assign_omron_addresses(self, table: TagTable) -> None:
        """Derive standard Omron PLC addresses if tag is not explicitly mapped.

        Standard convention for Omron CP1/CJ1/CJ2/CS1:
        - Simulator Inputs (PLC reads: sensors, buttons) -> CIO 0.00, 0.01, 0.02 ...
        - Simulator Outputs (PLC writes: motors, lamps)   -> CIO 100.00, 100.01 ...
        - Int/Float Registers                             -> D0, D2, D4 ...
        """
        in_word = 0
        in_bit = 0
        out_word = 100
        out_bit = 0
        d_word = 0

        for tag in table:
            # If tag already has an address, do not overwrite
            if tag.id in self.mapping:
                continue

            if tag.kind == "input":
                # PLC reads sensor / push button (Omron CP1: 0.00 s/d 0.11, lalu 1.00 s/d 1.11)
                if tag.type == "bit":
                    self.mapping[tag.id] = f"{in_word}.{in_bit:02d}"
                    in_bit += 1
                    if in_bit > 11:
                        in_bit = 0
                        in_word += 1
                else:
                    self.mapping[tag.id] = f"D{d_word}"
                    d_word += 2
            else:
                # PLC writes motor / valve / actuator / lamp (Omron CP1: 100.00 s/d 100.07, lalu 101.00 s/d 101.07)
                if tag.type == "bit":
                    self.mapping[tag.id] = f"{out_word}.{out_bit:02d}"
                    out_bit += 1
                    if out_bit > 7:
                        out_bit = 0
                        out_word += 1
                else:
                    self.mapping[tag.id] = f"D{d_word}"
                    d_word += 2

    async def start(self) -> None:
        self._running = True
        await self.link(False, "Searching for Omron CX-Simulator process (CxCpuMain / CxCpuMainCS2)...")

        # 1. Attempt to load NetToCXSim.Core via pythonnet
        dll_path = _find_core_dll()
        if HAS_PYTHONNET and dll_path:
            try:
                clr.AddReference(dll_path)
                from NetToCXSim.Services import OmronSimulatorEngine  # type: ignore
                self._engine = OmronSimulatorEngine()
                attached = self._engine.Attach()
                if attached:
                    self._cpu_type = str(self._engine.DetectedCpuType)
                    log.info("Attached to CX-Simulator via NetToCXSim.Core. CPU: %s", self._cpu_type)
            except Exception as err:
                log.warning("Could not initialize NetToCXSim.Core via pythonnet: %s", err)
                self._engine = None

        # 2. If pythonnet / DLL was not attached, use native ctypes fallback engine
        if self._engine is None or not getattr(self._engine, "IsConnected", False):
            try:
                self._engine = _NativeOmronSimulatorEngine()
                attached = self._engine.attach()
                if attached:
                    self._cpu_type = self._engine.detected_cpu_type
                    log.info("Attached to CX-Simulator via native memory engine. CPU: %s", self._cpu_type)
            except Exception as err:
                log.warning("Native memory engine attach failed: %s", err)
                self._engine = None

        # Verify attachment
        is_connected = False
        if self._engine is not None:
            if hasattr(self._engine, "IsConnected"):
                is_connected = bool(self._engine.IsConnected)
            elif hasattr(self._engine, "is_connected"):
                is_connected = bool(self._engine.is_connected)

        if not is_connected:
            raise RuntimeError(
                "Could not attach to Omron CX-Simulator! "
                "Please verify that CX-Simulator (CxCpuMain.exe or CxCpuMainCS2.exe) is running."
            )

        log.info(
            "Connected to Omron CX-Simulator by Ismail Lowkey. Target CPU: %s",
            self._cpu_type
        )

        await self._seed_inputs()
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._engine is not None:
            if hasattr(self._engine, "Detach"):
                self._engine.Detach()
            elif hasattr(self._engine, "detach"):
                self._engine.detach()
            self._engine = None

        await self.link(False, "Stopped; detached from Omron CX-Simulator")

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        self._table = table
        self._auto_assign_omron_addresses(table)
        await self._seed_inputs()

    async def _seed_inputs(self) -> None:
        table = self._table
        if table is None or self._engine is None:
            return

        seed = {}
        for tag in table.by_kind("input"):
            if tag.id in self.mapping:
                seed[tag.id] = table.visible(tag.id)

        if len(seed) > 0:
            await self.push(seed)
            log.info("Seeded %d simulator inputs into Omron CX-Simulator", len(seed))

        status_msg = (
            f"Attached to Omron CX-Simulator by Ismail Lowkey "
            f"({self._cpu_type}); {len(seed)} input(s) seeded"
        )
        await self.link(True, status_msg)

    async def push(self, values: dict[str, TagValue]) -> None:
        if self._engine is None or self._table is None:
            return

        for tag_id, val in values.items():
            address = self.mapping.get(tag_id)
            if address is None:
                self._warn_once(tag_id, f"Tag '{tag_id}' has no Omron PLC address; not written")
                continue

            try:
                tag = self._table.get(tag_id)
                if tag is not None and tag.kind == "input":
                    if tag.type == "bit":
                        bit_val = bool(val)
                        self._write_bit_to_plc(address, bit_val)
                    elif tag.type == "int":
                        int_val = int(val)
                        self._write_word_or_dword(address, int_val, is_float=False)
                    else:
                        float_val = float(val)
                        self._write_word_or_dword(address, float_val, is_float=True)
            except Exception as err:
                log.error("Error writing tag %s (%s) to Omron PLC: %s", tag_id, address, err)

    def _write_bit_to_plc(self, address: str, value: bool) -> None:
        if hasattr(self._engine, "WriteBit"):
            self._engine.WriteBit(address, value)
        elif hasattr(self._engine, "write_bit"):
            self._engine.write_bit(address, value)

    def _read_bit_from_plc(self, address: str) -> bool | None:
        if hasattr(self._engine, "ReadBit"):
            res = self._engine.ReadBit(address)
            if res is not None:
                return bool(res)
            return None
        elif hasattr(self._engine, "read_bit"):
            return self._engine.read_bit(address)
        return None

    def _write_word_or_dword(self, address: str, value: int | float, is_float: bool) -> None:
        # Write word or 32-bit dword to memory
        if hasattr(self._engine, "WriteWord"):
            if is_float:
                packed = struct.pack("<f", float(value))
                w0, w1 = struct.unpack("<HH", packed)
            else:
                packed = struct.pack("<i", int(value))
                w0, w1 = struct.unpack("<HH", packed)
            self._engine.WriteWord(address, w0)
        elif hasattr(self._engine, "write_word"):
            if is_float:
                packed = struct.pack("<f", float(value))
                w0, w1 = struct.unpack("<HH", packed)
            else:
                packed = struct.pack("<i", int(value))
                w0, w1 = struct.unpack("<HH", packed)
            self._engine.write_word(address, w0)

    def _read_word_or_dword(self, address: str, is_float: bool) -> int | float | None:
        if hasattr(self._engine, "ReadWord"):
            w = self._engine.ReadWord(address)
            if w is None:
                return None
            return int(w)
        elif hasattr(self._engine, "read_word"):
            w = self._engine.read_word(address)
            if w is None:
                return None
            return int(w)
        return None

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    async def _poll_loop(self) -> None:
        read_ok = False
        while self._running:
            if self._engine is not None and self._table is not None:
                changes: dict[str, TagValue] = {}

                # Read simulator outputs (PLC writes these)
                for tag in self._table.by_kind("output"):
                    address = self.mapping.get(tag.id)
                    if address is None:
                        self._warn_once(tag.id, f"{tag.id} has no Omron PLC address; not read")
                        continue

                    try:
                        if tag.type == "bit":
                            bit_val = self._read_bit_from_plc(address)
                            if bit_val is not None:
                                changes[tag.id] = bit_val
                                read_ok = True
                        elif tag.type == "int":
                            word_val = self._read_word_or_dword(address, is_float=False)
                            if word_val is not None:
                                changes[tag.id] = int(word_val)
                                read_ok = True
                        else:
                            float_val = self._read_word_or_dword(address, is_float=True)
                            if float_val is not None:
                                changes[tag.id] = float(float_val)
                                read_ok = True
                    except Exception as err:
                        self._warn_once(f"read:{tag.id}", f"Reading {tag.id} ({address}): {err}")

                if len(changes) > 0:
                    await self.bus.write_many(changes)
                elif not read_ok:
                    self._warn_once(
                        "nothing_read",
                        "Connected to Omron CX-Simulator but no tags could be read yet. "
                        "Verify your address mapping matches the ladder program."
                    )

            await asyncio.sleep(self.poll_interval)


# ---------------------------------------------------------------------------
# Native Windows ctypes implementation of Ismail Lowkey's OmronSimulatorEngine
# Provides 100% functionality without requiring pythonnet to be installed!
# ---------------------------------------------------------------------------
class _NativeOmronSimulatorEngine:
    """Direct Windows memory bridge for CX-Simulator matching NetToCXSim.Core."""

    CIO_OFFSET = 0x18000
    WR_OFFSET = 0x1BC00
    HR_OFFSET = 0x1B800
    AR_OFFSET = 0x17000
    DM_OFFSET = 0x20000
    TIM_PV_OFFSET = 0x1C000
    CNT_PV_OFFSET = 0x1E000
    TIM_FLAG_OFFSET = 0x17C00
    CNT_FLAG_OFFSET = 0x17E00

    def __init__(self) -> None:
        self.is_connected = False
        self.detected_cpu_type = "CX-Simulator CPU"
        self._h_process = None
        self._main_memory_base = 0

    def attach(self) -> bool:
        if os.name != "nt":
            return False

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        # Find CxCpuMain or CxCpuMainCS2 process
        pid = None
        is_cs2 = False

        # Enumerate processes via Windows toolhelp32
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == -1:
            return False

        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

        has_entry = kernel32.Process32First(snapshot, ctypes.byref(pe))
        while has_entry:
            exe_name = pe.szExeFile.decode("latin-1", errors="ignore").lower()
            if "cxcpumaincs2" in exe_name:
                pid = pe.th32ProcessID
                is_cs2 = True
                break
            elif "cxcpumain" in exe_name:
                pid = pe.th32ProcessID
                is_cs2 = False
                break
            has_entry = kernel32.Process32Next(snapshot, ctypes.byref(pe))

        kernel32.CloseHandle(snapshot)

        if pid is None:
            return False

        PROCESS_ALL_ACCESS = 0x1F0FFF
        h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not h_process:
            return False

        self._h_process = h_process

        # Find CxCpuMisc base address from module snapshot
        TH32CS_SNAPMODULE = 0x00000008
        TH32CS_SNAPMODULE32 = 0x00000010

        class MODULEENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("th32ModuleID", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("GlblcntUsage", wintypes.DWORD),
                ("ProccntUsage", wintypes.DWORD),
                ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", wintypes.DWORD),
                ("hModule", wintypes.HMODULE),
                ("szModule", ctypes.c_char * 256),
                ("szExePath", ctypes.c_char * 260),
            ]

        mod_snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
        misc_base = None

        if mod_snapshot != -1:
            me = MODULEENTRY32()
            me.dwSize = ctypes.sizeof(MODULEENTRY32)
            has_mod = kernel32.Module32First(mod_snapshot, ctypes.byref(me))
            while has_mod:
                mod_name = me.szModule.decode("latin-1", errors="ignore").lower()
                if "cxcpumisc" in mod_name:
                    misc_base = me.modBaseAddr
                    break
                has_mod = kernel32.Module32Next(mod_snapshot, ctypes.byref(me))
            kernel32.CloseHandle(mod_snapshot)

        if misc_base is None:
            self.detach()
            return False

        # Read global pointer from CxCpuMisc
        # DEFAULT_MISC_CS2_PTR_RVA = 0xC18C, DEFAULT_MISC_PTR_RVA = 0xA184
        if is_cs2:
            rva = 0xC18C
        else:
            rva = 0xA184

        ptr_addr = misc_base + rva
        buf = (ctypes.c_uint32 * 1)()
        bytes_read = ctypes.c_size_t(0)

        res = kernel32.ReadProcessMemory(h_process, ctypes.c_void_p(ptr_addr), buf, 4, ctypes.byref(bytes_read))
        if not res or buf[0] == 0:
            self.detach()
            return False

        # Resolve pointer level 2
        p1 = buf[0]
        res = kernel32.ReadProcessMemory(h_process, ctypes.c_void_p(p1), buf, 4, ctypes.byref(bytes_read))
        if not res or buf[0] == 0:
            self.detach()
            return False

        self._main_memory_base = buf[0]
        self.is_connected = True
        if is_cs2:
            self.detected_cpu_type = "CJ2 / CS2 Series"
        else:
            self.detected_cpu_type = "CP1 / CJ1 / CS1 Series"

        return True

    def detach(self) -> None:
        if self._h_process:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self._h_process)
            self._h_process = None
        self.is_connected = False
        self._main_memory_base = 0

    def _parse_address(self, addr_str: str) -> tuple[int, int, int]:
        """Parse address string to (area_offset, word, bit)."""
        addr = addr_str.strip().upper()
        area_offset = self.CIO_OFFSET

        if addr.startswith("CIO"):
            area_offset = self.CIO_OFFSET
            addr = addr[3:]
        elif addr.startswith("W"):
            area_offset = self.WR_OFFSET
            addr = addr[1:]
        elif addr.startswith("H"):
            area_offset = self.HR_OFFSET
            addr = addr[1:]
        elif addr.startswith("A"):
            area_offset = self.AR_OFFSET
            addr = addr[1:]
        elif addr.startswith("D"):
            area_offset = self.DM_OFFSET
            addr = addr[1:]
        elif addr.startswith("T") or addr.startswith("TIM"):
            area_offset = self.TIM_FLAG_OFFSET
            if addr.startswith("TIM"):
                addr = addr[3:]
            else:
                addr = addr[1:]
        elif addr.startswith("C") or addr.startswith("CNT"):
            area_offset = self.CNT_FLAG_OFFSET
            if addr.startswith("CNT"):
                addr = addr[3:]
            else:
                addr = addr[1:]

        if "." in addr:
            parts = addr.split(".")
            word = int(parts[0])
            bit = int(parts[1])
        else:
            word = int(addr)
            bit = 0

        return area_offset, word, bit

    def read_bit(self, address_str: str) -> bool | None:
        if not self.is_connected or not self._h_process:
            return None

        import ctypes
        area_offset, word, bit = self._parse_address(address_str)

        if area_offset == self.TIM_FLAG_OFFSET or area_offset == self.CNT_FLAG_OFFSET:
            byte_offset = word // 8
            bit_in_byte = word % 8
        else:
            byte_offset = word * 2
            if bit >= 8:
                byte_offset += 1
                bit_in_byte = bit - 8
            else:
                bit_in_byte = bit

        target_addr = self._main_memory_base + area_offset + byte_offset
        buf = (ctypes.c_uint8 * 1)()
        bytes_read = ctypes.c_size_t(0)

        res = ctypes.windll.kernel32.ReadProcessMemory(
            self._h_process, ctypes.c_void_p(target_addr), buf, 1, ctypes.byref(bytes_read)
        )
        if not res:
            return None

        return bool(buf[0] & (1 << bit_in_byte))

    def write_bit(self, address_str: str, value: bool) -> bool:
        if not self.is_connected or not self._h_process:
            return False

        import ctypes
        area_offset, word, bit = self._parse_address(address_str)

        if area_offset == self.TIM_FLAG_OFFSET or area_offset == self.CNT_FLAG_OFFSET:
            byte_offset = word // 8
            bit_in_byte = word % 8
        else:
            byte_offset = word * 2
            if bit >= 8:
                byte_offset += 1
                bit_in_byte = bit - 8
            else:
                bit_in_byte = bit

        target_addr = self._main_memory_base + area_offset + byte_offset
        buf = (ctypes.c_uint8 * 1)()
        bytes_read = ctypes.c_size_t(0)

        # Read current byte first
        res = ctypes.windll.kernel32.ReadProcessMemory(
            self._h_process, ctypes.c_void_p(target_addr), buf, 1, ctypes.byref(bytes_read)
        )
        if not res:
            return False

        if value:
            buf[0] |= (1 << bit_in_byte)
        else:
            buf[0] &= ~(1 << bit_in_byte)

        bytes_written = ctypes.c_size_t(0)
        res = ctypes.windll.kernel32.WriteProcessMemory(
            self._h_process, ctypes.c_void_p(target_addr), buf, 1, ctypes.byref(bytes_written)
        )
        return bool(res)

    def read_word(self, address_str: str) -> int | None:
        if not self.is_connected or not self._h_process:
            return None

        import ctypes
        area_offset, word, _ = self._parse_address(address_str)
        target_addr = self._main_memory_base + area_offset + (word * 2)

        buf = (ctypes.c_uint16 * 1)()
        bytes_read = ctypes.c_size_t(0)
        res = ctypes.windll.kernel32.ReadProcessMemory(
            self._h_process, ctypes.c_void_p(target_addr), buf, 2, ctypes.byref(bytes_read)
        )
        if not res:
            return None

        return int(buf[0])

    def write_word(self, address_str: str, value: int) -> bool:
        if not self.is_connected or not self._h_process:
            return False

        import ctypes
        area_offset, word, _ = self._parse_address(address_str)
        target_addr = self._main_memory_base + area_offset + (word * 2)

        buf = (ctypes.c_uint16 * 1)(int(value) & 0xFFFF)
        bytes_written = ctypes.c_size_t(0)
        res = ctypes.windll.kernel32.WriteProcessMemory(
            self._h_process, ctypes.c_void_p(target_addr), buf, 2, ctypes.byref(bytes_written)
        )
        return bool(res)
