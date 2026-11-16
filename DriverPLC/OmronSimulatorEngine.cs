using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace NetToCXSim.Services
{
    public class OmronSimulatorEngine : IDisposable
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(int dwDesiredAccess, bool bInheritHandle, int dwProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ReadProcessMemory(IntPtr hProcess, IntPtr lpBaseAddress, [Out] byte[] lpBuffer, int dwSize, out IntPtr lpNumberOfBytesRead);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool WriteProcessMemory(IntPtr hProcess, IntPtr lpBaseAddress, byte[] lpBuffer, int dwSize, out IntPtr lpNumberOfBytesWritten);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Ansi)]
        private static extern IntPtr OpenFileMappingA(uint dwDesiredAccess, bool bInheritHandle, string lpName);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr MapViewOfFile(IntPtr hFileMappingObject, uint dwDesiredAccess, uint dwFileOffsetHigh, uint dwFileOffsetLow, UIntPtr dwNumberOfBytesToMap);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UnmapViewOfFile(IntPtr lpBaseAddress);

        private const int PROCESS_VM_READ = 0x0010;
        private const int PROCESS_VM_WRITE = 0x0020;
        private const int PROCESS_VM_OPERATION = 0x0008;
        private const uint FILE_MAP_READ = 0x0004;

        private IntPtr _hProcess = IntPtr.Zero;
        private int _processId = 0;
        private string _processPath = "";
        private string _detectedCpuType = "";
        private string _miscModuleName = "";
        private bool _isCs2Engine = false;

        // The actual live memory base pointer of CxCpuMain / CxCpuMainCS2
        private IntPtr _mainMemoryBase = IntPtr.Zero;
        private IntPtr _cxMiscBase = IntPtr.Zero;
        private IntPtr _cxSizeBase = IntPtr.Zero;
        private IntPtr _cxOsRoutineBase = IntPtr.Zero;
        private int _exSysCpuTypeRva = 0;

        // Omron internal area offsets from main memory base (verified live from CX-Simulator engine)
        private const int TIM_PV_OFFSET   = 0x1C000; // Timer Present Value (4096 words, T0-T4095)
        private const int CNT_PV_OFFSET   = 0x1E000; // Counter Present Value (4096 words, C0-C4095)
        private const int TIM_FLAG_OFFSET = 0x17C00; // Timer Completion Flags (4096 bits)
        private const int CNT_FLAG_OFFSET = 0x17E00; // Counter Completion Flags (4096 bits)
        private const int CIO_OFFSET      = 0x18000;
        private const int HR_OFFSET       = 0x1B800;
        private const int WR_OFFSET       = 0x1BC00;
        private const int AR_OFFSET       = 0x17000;
        private const int DM_OFFSET       = 0x20000;
        private const int EM_OFFSET       = 0x30000;

        // Fallback RVAs if PE export table resolution is unavailable
        private const int DEFAULT_MISC_PTR_RVA     = 0xA184; // CxCpuMisc.dll
        private const int DEFAULT_MISC_CS2_PTR_RVA = 0xC18C; // CxCpuMiscCS2.dll

        public bool IsConnected => _hProcess != IntPtr.Zero && _mainMemoryBase != IntPtr.Zero;
        public int ProcessId => _processId;
        public string ProcessPath => _processPath;
        public string DetectedCpuType => _detectedCpuType;
        public string MiscModuleName => _miscModuleName;
        public bool IsCs2Engine => _isCs2Engine;

        public bool Attach()
        {
            try
            {
                // Check both simulator engines: CxCpuMain (CP1/CJ1/CS1) and CxCpuMainCS2 (CJ2/CS2)
                Process proc = null;
                bool isCs2 = false;

                Process[] mainProcs = Process.GetProcessesByName("CxCpuMain");
                if (mainProcs.Length > 0)
                {
                    proc = mainProcs[0];
                    isCs2 = false;
                }
                else
                {
                    Process[] cs2Procs = Process.GetProcessesByName("CxCpuMainCS2");
                    if (cs2Procs.Length > 0)
                    {
                        proc = cs2Procs[0];
                        isCs2 = true;
                    }
                }

                if (proc == null)
                {
                    Detach();
                    return false;
                }

                if (_hProcess != IntPtr.Zero && _processId == proc.Id && _mainMemoryBase != IntPtr.Zero)
                {
                    // Refresh CPU model name from ExSys_cpu_type (updates after project download)
                    string osCpuType = ReadOsRoutineCpuType();
                    if (!string.IsNullOrEmpty(osCpuType))
                    {
                        _detectedCpuType = osCpuType;
                    }
                    else if (string.IsNullOrEmpty(_detectedCpuType) || _detectedCpuType == "CX-Simulator CPU" || _detectedCpuType == "CJ2 / CS2 Series")
                    {
                        string refreshedName = DetectPlcCpuModel();
                        if (!string.IsNullOrEmpty(refreshedName))
                        {
                            _detectedCpuType = refreshedName;
                        }
                    }
                    return true;
                }

                Detach();

                _processId = proc.Id;
                _isCs2Engine = isCs2;
                if (isCs2)
                {
                    _processPath = "CxCpuMainCS2.exe";
                }
                else
                {
                    _processPath = "CxCpuMain.exe";
                }

                _hProcess = OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION, false, _processId);
                if (_hProcess == IntPtr.Zero)
                {
                    Detach();
                    return false;
                }

                string targetMiscDll = "CxCpuMisc.dll";
                string targetSizeDll = "CxCpuSize.dll";
                string targetOsRoutineDll = "CxOsRoutine.dll";
                if (isCs2)
                {
                    targetMiscDll = "CxCpuMiscCS2.dll";
                    targetSizeDll = "CxCpuSizeCS2.dll";
                    targetOsRoutineDll = "CxOsRoutineCS2.dll";
                }

                // Locate CxCpuMisc*.dll, CxCpuSize*.dll, and CxOsRoutine*.dll module base addresses
                foreach (ProcessModule m in proc.Modules)
                {
                    if (m.ModuleName.Equals(targetMiscDll, StringComparison.OrdinalIgnoreCase))
                    {
                        _cxMiscBase = m.BaseAddress;
                        _miscModuleName = m.ModuleName;
                    }
                    else if (m.ModuleName.Equals(targetSizeDll, StringComparison.OrdinalIgnoreCase))
                    {
                        _cxSizeBase = m.BaseAddress;
                    }
                    else if (m.ModuleName.Equals(targetOsRoutineDll, StringComparison.OrdinalIgnoreCase))
                    {
                        _cxOsRoutineBase = m.BaseAddress;
                    }
                }

                if (_cxOsRoutineBase != IntPtr.Zero)
                {
                    _exSysCpuTypeRva = ResolveModuleExportRva(_cxOsRoutineBase, "ExSys_cpu_type");
                }

                if (_cxMiscBase == IntPtr.Zero)
                {
                    Detach();
                    return false;
                }

                // Dynamically resolve global memory pointer from exported CpuClearCio function
                IntPtr globalPtrAddr = ResolveMemoryBasePointer(_cxMiscBase, _isCs2Engine);
                if (globalPtrAddr == IntPtr.Zero)
                {
                    Detach();
                    return false;
                }

                byte[] ptrBuf = new byte[4];
                if (!ReadProcessMemory(_hProcess, globalPtrAddr, ptrBuf, 4, out _))
                {
                    Detach();
                    return false;
                }

                uint p1 = BitConverter.ToUInt32(ptrBuf, 0);
                if (p1 == 0)
                {
                    Detach();
                    return false;
                }

                if (!ReadProcessMemory(_hProcess, (IntPtr)p1, ptrBuf, 4, out _))
                {
                    Detach();
                    return false;
                }

                uint pBase = BitConverter.ToUInt32(ptrBuf, 0);
                if (pBase == 0)
                {
                    Detach();
                    return false;
                }

                _mainMemoryBase = (IntPtr)pBase;

                // Auto-detect active PLC CPU model name (e.g., CP1E-E40DR-A, CJ1M-CPU11, CS1H-CPU67, CJ2M-CPU33)
                string modelName = DetectPlcCpuModel();

                if (!string.IsNullOrEmpty(modelName))
                {
                    _detectedCpuType = modelName;
                }
                else if (_isCs2Engine)
                {
                    _detectedCpuType = "CJ2 / CS2 Series";
                }
                else
                {
                    _detectedCpuType = "CX-Simulator CPU";
                }

                return true;
            }
            catch
            {
                Detach();
                return false;
            }
        }

        public void Detach()
        {
            if (_hProcess != IntPtr.Zero)
            {
                CloseHandle(_hProcess);
                _hProcess = IntPtr.Zero;
            }
            _processId = 0;
            _processPath = "";
            _detectedCpuType = "";
            _miscModuleName = "";
            _isCs2Engine = false;
            _mainMemoryBase = IntPtr.Zero;
            _cxMiscBase = IntPtr.Zero;
            _cxSizeBase = IntPtr.Zero;
            _cxOsRoutineBase = IntPtr.Zero;
            _exSysCpuTypeRva = 0;
        }

        /// <summary>
        /// Dynamically resolves the global memory pointer address from CxCpuMisc*.dll's exported CpuClearCio function.
        /// Falls back to known build RVAs if export parsing fails.
        /// </summary>
        private IntPtr ResolveMemoryBasePointer(IntPtr cxMiscBase, bool isCs2)
        {
            int clearCioRva = ResolveModuleExportRva(cxMiscBase, "CpuClearCio");
            if (clearCioRva > 0)
            {
                byte[] code = new byte[16];
                IntPtr funcAddr = (IntPtr)((long)cxMiscBase + clearCioRva);
                if (ReadProcessMemory(_hProcess, funcAddr, code, code.Length, out _))
                {
                    // CpuClearCio starts with: A1 <4-byte VA> (mov eax, ds:[globalPtr])
                    if (code[0] == 0xA1)
                    {
                        uint va = BitConverter.ToUInt32(code, 1);
                        if (va >= 0x10000)
                        {
                            return (IntPtr)va;
                        }
                    }
                }
            }

            if (isCs2)
            {
                return (IntPtr)((long)cxMiscBase + DEFAULT_MISC_CS2_PTR_RVA);
            }
            else
            {
                return (IntPtr)((long)cxMiscBase + DEFAULT_MISC_PTR_RVA);
            }
        }

        /// <summary>
        /// Reads the active PLC CPU type (e.g. "CJ1M-CPU11") from CxOsRoutine*.dll's exported ExSys_cpu_type buffer.
        /// </summary>
        private string ReadOsRoutineCpuType()
        {
            if (_hProcess == IntPtr.Zero || _cxOsRoutineBase == IntPtr.Zero || _exSysCpuTypeRva <= 0)
            {
                return "";
            }

            byte[] buf = new byte[20];
            IntPtr addr = (IntPtr)((long)_cxOsRoutineBase + _exSysCpuTypeRva);
            if (!ReadProcessMemory(_hProcess, addr, buf, buf.Length, out _))
            {
                return "";
            }

            int len = 0;
            while (len < buf.Length && buf[len] != 0)
            {
                if (buf[len] < 0x20 || buf[len] > 0x7E)
                {
                    return "";
                }
                len++;
            }

            if (len == 0)
            {
                return "";
            }

            string raw = System.Text.Encoding.ASCII.GetString(buf, 0, len).Trim();
            if (string.IsNullOrEmpty(raw))
            {
                return "";
            }

            return raw.Replace('_', '-');
        }

        /// <summary>
        /// Detects the actual PLC CPU model (e.g. CJ1M-CPU11, CP1E-E40DR-A, CS1H-CPU67, CJ2M-CPU33).
        /// Prefers CxOsRoutine*.dll's ExSys_cpu_type (downloaded project CPU) and falls back to MainExPLCModel shared memory.
        /// </summary>
        private string DetectPlcCpuModel()
        {
            string osCpuType = ReadOsRoutineCpuType();
            if (!string.IsNullOrEmpty(osCpuType))
            {
                return osCpuType;
            }

            string instanceName = "";
            if (_cxSizeBase != IntPtr.Zero)
            {
                instanceName = ReadPlcInstanceName(_cxSizeBase);
            }

            if (!string.IsNullOrEmpty(instanceName))
            {
                string model = ReadPlcModelFromSharedMemory(instanceName + "mainPropertyMemory");
                if (!string.IsNullOrEmpty(model))
                {
                    return model;
                }
            }

            string fallbackModel = ReadPlcModelFromSharedMemory("SimulatorPLCmainPropertyMemory");
            if (!string.IsNullOrEmpty(fallbackModel))
            {
                return fallbackModel;
            }

            return ReadPlcModelFromSharedMemory("OMRON PLC simulatormainPropertyMemory");
        }

        private static string ReadPlcModelFromSharedMemory(string mappingName)
        {
            IntPtr hMap = OpenFileMappingA(FILE_MAP_READ, false, mappingName);
            if (hMap == IntPtr.Zero)
            {
                return "";
            }

            try
            {
                IntPtr pView = MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, (UIntPtr)32);
                if (pView == IntPtr.Zero)
                {
                    return "";
                }

                try
                {
                    byte[] buf = new byte[20];
                    Marshal.Copy(pView, buf, 0, buf.Length);

                    int len = 0;
                    while (len < buf.Length && buf[len] != 0)
                    {
                        if (buf[len] < 0x20 || buf[len] > 0x7E)
                        {
                            return "";
                        }
                        len++;
                    }

                    if (len == 0)
                    {
                        return "";
                    }

                    return System.Text.Encoding.ASCII.GetString(buf, 0, len).Trim();
                }
                finally
                {
                    UnmapViewOfFile(pView);
                }
            }
            finally
            {
                CloseHandle(hMap);
            }
        }

        /// <summary>
        /// Reads the simulator instance name (e.g. "SimulatorPLC") from CxCpuSize*.dll's exported CpuPlcName::m_plcName.
        /// </summary>
        private string ReadPlcInstanceName(IntPtr cxSizeBase)
        {
            int plcNameRva = ResolveModuleExportRva(cxSizeBase, "m_plcName@CpuPlcName");
            if (plcNameRva <= 0)
            {
                return "";
            }

            byte[] strStruct = new byte[28];
            IntPtr strAddr = (IntPtr)((long)cxSizeBase + plcNameRva);
            if (!ReadProcessMemory(_hProcess, strAddr, strStruct, strStruct.Length, out _))
            {
                return "";
            }

            // 1. Check VC6 std::string layout: +0x04 = _Ptr, +0x08 = _Len, +0x0C = _Res
            uint vc6Ptr = BitConverter.ToUInt32(strStruct, 4);
            uint vc6Len = BitConverter.ToUInt32(strStruct, 8);
            uint vc6Res = BitConverter.ToUInt32(strStruct, 12);
            if (vc6Ptr >= 0x10000 && vc6Len > 0 && vc6Len <= 64 && vc6Res >= vc6Len && vc6Res <= 256)
            {
                byte[] textBuf = new byte[vc6Len];
                if (ReadProcessMemory(_hProcess, (IntPtr)vc6Ptr, textBuf, (int)vc6Len, out _))
                {
                    string candidate = ExtractPrintableAscii(textBuf);
                    if (!string.IsNullOrEmpty(candidate))
                    {
                        return candidate;
                    }
                }
            }

            // 2. Check VC7+ SSO std::string layout: +0x04 = inline buf or heap ptr, +0x14 = _Mysize, +0x18 = _Myres
            uint ssoSize = BitConverter.ToUInt32(strStruct, 20);
            uint ssoRes = BitConverter.ToUInt32(strStruct, 24);
            if (ssoSize > 0 && ssoSize <= 64 && ssoRes >= ssoSize && ssoRes <= 256)
            {
                if (ssoRes < 16)
                {
                    byte[] inlineBuf = new byte[ssoSize];
                    Buffer.BlockCopy(strStruct, 4, inlineBuf, 0, (int)ssoSize);
                    string candidate = ExtractPrintableAscii(inlineBuf);
                    if (!string.IsNullOrEmpty(candidate))
                    {
                        return candidate;
                    }
                }
                else
                {
                    uint heapPtr = BitConverter.ToUInt32(strStruct, 4);
                    if (heapPtr >= 0x10000)
                    {
                        byte[] textBuf = new byte[ssoSize];
                        if (ReadProcessMemory(_hProcess, (IntPtr)heapPtr, textBuf, (int)ssoSize, out _))
                        {
                            string candidate = ExtractPrintableAscii(textBuf);
                            if (!string.IsNullOrEmpty(candidate))
                            {
                                return candidate;
                            }
                        }
                    }
                }
            }

            return "";
        }

        private static string ExtractPrintableAscii(byte[] buf)
        {
            if (buf == null || buf.Length == 0) return "";
            for (int i = 0; i < buf.Length; i++)
            {
                if (buf[i] < 0x20 || buf[i] > 0x7E)
                {
                    return "";
                }
            }
            return System.Text.Encoding.ASCII.GetString(buf).Trim();
        }

        /// <summary>
        /// Parses the PE Export Directory of a loaded 32-bit module in the target process to locate an export RVA.
        /// </summary>
        private int ResolveModuleExportRva(IntPtr moduleBase, string partialName)
        {
            byte[] header = new byte[0x400];
            if (!ReadProcessMemory(_hProcess, moduleBase, header, header.Length, out _))
            {
                return 0;
            }

            // Verify DOS "MZ" signature
            if (header[0] != 0x4D || header[1] != 0x5A) return 0;
            int peOffset = BitConverter.ToInt32(header, 0x3C);
            if (peOffset <= 0 || peOffset + 0x80 > header.Length) return 0;

            // Verify PE\0\0 signature
            if (header[peOffset] != 0x50 || header[peOffset + 1] != 0x45 || header[peOffset + 2] != 0 || header[peOffset + 3] != 0)
            {
                return 0;
            }

            int optOffset = peOffset + 24;
            ushort magic = BitConverter.ToUInt16(header, optOffset);
            if (magic != 0x010B) return 0; // PE32 only

            int exportRva = BitConverter.ToInt32(header, optOffset + 96);
            if (exportRva <= 0) return 0;

            byte[] expDir = new byte[40];
            if (!ReadProcessMemory(_hProcess, (IntPtr)((long)moduleBase + exportRva), expDir, expDir.Length, out _))
            {
                return 0;
            }

            int numNames = BitConverter.ToInt32(expDir, 24);
            int addrFuncsRva = BitConverter.ToInt32(expDir, 28);
            int addrNamesRva = BitConverter.ToInt32(expDir, 32);
            int addrOrdsRva = BitConverter.ToInt32(expDir, 36);

            if (numNames <= 0 || numNames > 2048) return 0;

            byte[] nameRvasBuf = new byte[numNames * 4];
            byte[] ordsBuf = new byte[numNames * 2];
            if (!ReadProcessMemory(_hProcess, (IntPtr)((long)moduleBase + addrNamesRva), nameRvasBuf, nameRvasBuf.Length, out _))
            {
                return 0;
            }
            if (!ReadProcessMemory(_hProcess, (IntPtr)((long)moduleBase + addrOrdsRva), ordsBuf, ordsBuf.Length, out _))
            {
                return 0;
            }

            byte[] nameStrBuf = new byte[128];
            for (int i = 0; i < numNames; i++)
            {
                int nameRva = BitConverter.ToInt32(nameRvasBuf, i * 4);
                if (nameRva <= 0) continue;

                if (!ReadProcessMemory(_hProcess, (IntPtr)((long)moduleBase + nameRva), nameStrBuf, nameStrBuf.Length, out _))
                {
                    continue;
                }

                int len = 0;
                while (len < nameStrBuf.Length && nameStrBuf[len] != 0)
                {
                    len++;
                }

                string exportName = System.Text.Encoding.ASCII.GetString(nameStrBuf, 0, len);
                if (exportName.IndexOf(partialName, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    ushort ord = BitConverter.ToUInt16(ordsBuf, i * 2);
                    byte[] funcRvaBuf = new byte[4];
                    if (ReadProcessMemory(_hProcess, (IntPtr)((long)moduleBase + addrFuncsRva + ord * 4), funcRvaBuf, 4, out _))
                    {
                        return BitConverter.ToInt32(funcRvaBuf, 0);
                    }
                }
            }

            return 0;
        }

        /// <summary>
        /// Reads a bit from live simulator memory.
        /// Supports: "100.0", "100.00", "0.0", "CIO100.0", "W0.0", "H0.0", "D0.0", "T0", "TIM0", "C0", "CNT0"
        /// </summary>
        public bool? ReadBit(string address)
        {
            if (!IsConnected && !Attach()) return null;

            if (TryParseAddress(address, out MemoryArea area, out int word, out int bit))
            {
                if (area == MemoryArea.TIM_PV) area = MemoryArea.TIM_FLAG;
                else if (area == MemoryArea.CNT_PV) area = MemoryArea.CNT_FLAG;

                return ReadBit(area, word, bit);
            }
            return null;
        }

        /// <summary>
        /// Writes a bit to live simulator memory.
        /// </summary>
        public bool WriteBit(string address, bool value)
        {
            if (!IsConnected && !Attach()) return false;

            if (TryParseAddress(address, out MemoryArea area, out int word, out int bit))
            {
                if (area == MemoryArea.TIM_PV) area = MemoryArea.TIM_FLAG;
                else if (area == MemoryArea.CNT_PV) area = MemoryArea.CNT_FLAG;

                return WriteBit(area, word, bit, value);
            }
            return false;
        }

        public bool WriteBit(MemoryArea area, int word, int bit, bool value)
        {
            if (!IsConnected && !Attach()) return false;

            int areaOffset = GetAreaOffset(area);
            IntPtr targetAddr;
            int bitInByte;

            if (area == MemoryArea.TIM_FLAG || area == MemoryArea.CNT_FLAG)
            {
                int bitIndex = word;
                int byteOffset = bitIndex / 8;
                bitInByte = bitIndex % 8;
                targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset);
            }
            else
            {
                int byteOffset = word * 2;
                bitInByte = bit >= 8 ? (bit - 8) : bit;
                targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset + (bit >= 8 ? 1 : 0));
            }

            byte[] buf = new byte[1];
            if (ReadProcessMemory(_hProcess, targetAddr, buf, 1, out _))
            {
                if (value)
                    buf[0] |= (byte)(1 << bitInByte);
                else
                    buf[0] &= (byte)~(1 << bitInByte);

                return WriteProcessMemory(_hProcess, targetAddr, buf, 1, out _);
            }
            return false;
        }

        public ushort? ReadWord(MemoryArea area, int word)
        {
            if (!IsConnected && !Attach()) return null;

            int areaOffset = GetAreaOffset(area);
            int byteOffset = word * 2;
            IntPtr targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset);

            byte[] buf = new byte[2];
            if (ReadProcessMemory(_hProcess, targetAddr, buf, 2, out _))
            {
                return (ushort)(buf[0] | (buf[1] << 8));
            }
            return null;
        }

        public ushort? ReadWord(string address)
        {
            if (!IsConnected && !Attach()) return null;

            if (TryParseAddress(address, out MemoryArea area, out int word, out _))
            {
                return ReadWord(area, word);
            }
            return null;
        }

        public bool WriteWord(MemoryArea area, int word, ushort value)
        {
            if (!IsConnected && !Attach()) return false;

            int areaOffset = GetAreaOffset(area);
            int byteOffset = word * 2;
            IntPtr targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset);

            byte[] buf = new byte[2] { (byte)(value & 0xFF), (byte)((value >> 8) & 0xFF) };
            return WriteProcessMemory(_hProcess, targetAddr, buf, 2, out _);
        }

        /// <summary>
        /// Writes a 16-bit word to live simulator memory.
        /// </summary>
        public bool WriteWord(string address, ushort value)
        {
            if (!IsConnected && !Attach()) return false;

            if (TryParseAddress(address, out MemoryArea area, out int word, out _))
            {
                return WriteWord(area, word, value);
            }
            return false;
        }

        public bool? ReadBit(MemoryArea area, int word, int bit)
        {
            if (!IsConnected && !Attach()) return null;

            int areaOffset = GetAreaOffset(area);
            IntPtr targetAddr;
            int bitInByte;

            if (area == MemoryArea.TIM_FLAG || area == MemoryArea.CNT_FLAG)
            {
                int bitIndex = word;
                int byteOffset = bitIndex / 8;
                bitInByte = bitIndex % 8;
                targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset);
            }
            else
            {
                int byteOffset = word * 2;
                bitInByte = bit >= 8 ? (bit - 8) : bit;
                targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset + (bit >= 8 ? 1 : 0));
            }

            byte[] buf = new byte[1];
            if (ReadProcessMemory(_hProcess, targetAddr, buf, 1, out _))
            {
                return (buf[0] & (1 << bitInByte)) != 0;
            }
            return null;
        }

        public byte[] ReadBytes(MemoryArea area, int wordOffset, int byteCount)
        {
            if (!IsConnected && !Attach()) return null;

            int areaOffset = GetAreaOffset(area);
            int byteOffset = wordOffset * 2;
            IntPtr targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset);

            byte[] buf = new byte[byteCount];
            if (ReadProcessMemory(_hProcess, targetAddr, buf, byteCount, out _))
            {
                return buf;
            }
            return null;
        }

        public bool WriteBytes(MemoryArea area, int wordOffset, byte[] data)
        {
            if (!IsConnected && !Attach()) return false;
            if (data == null || data.Length == 0) return true;

            int areaOffset = GetAreaOffset(area);
            int byteOffset = wordOffset * 2;
            IntPtr targetAddr = (IntPtr)((long)_mainMemoryBase + areaOffset + byteOffset);

            return WriteProcessMemory(_hProcess, targetAddr, data, data.Length, out _);
        }

        public int GetAreaOffset(MemoryArea area)
        {
            switch (area)
            {
                case MemoryArea.CIO:
                    return CIO_OFFSET;      // 0x18000
                case MemoryArea.WR:
                    return WR_OFFSET;       // 0x1BC00
                case MemoryArea.HR:
                    return HR_OFFSET;       // 0x1B800
                case MemoryArea.AR:
                    return AR_OFFSET;       // 0x17000
                case MemoryArea.DM:
                    return DM_OFFSET;       // 0x20000
                case MemoryArea.EM:
                    return EM_OFFSET;       // 0x30000
                case MemoryArea.TIM_PV:
                    return TIM_PV_OFFSET;   // 0x1C000
                case MemoryArea.CNT_PV:
                    return CNT_PV_OFFSET;   // 0x1E000
                case MemoryArea.TIM_FLAG:
                    return TIM_FLAG_OFFSET; // 0x17C00
                case MemoryArea.CNT_FLAG:
                    return CNT_FLAG_OFFSET; // 0x17E00
                default:
                    return CIO_OFFSET;
            }
        }

        public enum MemoryArea
        {
            CIO,
            WR,
            HR,
            AR,
            DM,
            EM,
            TIM_PV,
            CNT_PV,
            TIM_FLAG,
            CNT_FLAG
        }

        public static bool TryParseAddress(string addrStr, out MemoryArea area, out int word, out int bit)
        {
            area = MemoryArea.CIO;
            word = 0;
            bit = 0;

            if (string.IsNullOrWhiteSpace(addrStr)) return false;
            addrStr = addrStr.Trim().ToUpperInvariant();

            if (addrStr.StartsWith("CIO"))
            {
                area = MemoryArea.CIO;
                addrStr = addrStr.Substring(3);
            }
            else if (addrStr.StartsWith("TIM"))
            {
                area = MemoryArea.TIM_PV;
                addrStr = addrStr.Substring(3);
            }
            else if (addrStr.StartsWith("CNT"))
            {
                area = MemoryArea.CNT_PV;
                addrStr = addrStr.Substring(3);
            }
            else if (addrStr.StartsWith("W"))
            {
                area = MemoryArea.WR;
                addrStr = addrStr.Substring(1);
            }
            else if (addrStr.StartsWith("H"))
            {
                area = MemoryArea.HR;
                addrStr = addrStr.Substring(1);
            }
            else if (addrStr.StartsWith("D"))
            {
                area = MemoryArea.DM;
                addrStr = addrStr.Substring(1);
            }
            else if (addrStr.StartsWith("T"))
            {
                area = MemoryArea.TIM_PV;
                addrStr = addrStr.Substring(1);
            }
            else if (addrStr.StartsWith("C"))
            {
                area = MemoryArea.CNT_PV;
                addrStr = addrStr.Substring(1);
            }
            else if (char.IsDigit(addrStr[0]))
            {
                area = MemoryArea.CIO;
            }

            if (addrStr.Contains("."))
            {
                var parts = addrStr.Split('.');
                if (int.TryParse(parts[0], out word) && int.TryParse(parts[1], out bit))
                {
                    return true;
                }
                return false;
            }
            else
            {
                if (int.TryParse(addrStr, out word))
                {
                    bit = 0;
                    return true;
                }
                return false;
            }
        }

        public void Dispose()
        {
            Detach();
        }
    }
}
