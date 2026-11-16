using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using Microsoft.Win32;

namespace NetToCXSim.Services
{
    public static class OpcDaConstants
    {
        public static readonly Guid CLSID_NetToCxSimOpcDa = new Guid("B5D2D68C-7975-4B86-B909-64DE9B518F8C");
        public static readonly Guid CATID_OPCDAServer10   = new Guid("63D5F430-CFE4-11d1-B2C8-0060083BA1FB");
        public static readonly Guid CATID_OPCDAServer20   = new Guid("63D5F432-CFE4-11d1-B2C8-0060083BA1FB");

        public const string ProgId = "NetToCxSim.OPCServer.DA";
        public const string ServerDescription = "NetToCxSim Omron CX-Simulator OPC DA Server";

        public const short OPC_QUALITY_BAD     = 0x00;
        public const short OPC_QUALITY_GOOD    = 0xC0;

        public const int S_OK                  = 0;
        public const int S_FALSE               = 1;
        public const int E_FAIL                = unchecked((int)0x80004005);
        public const int E_NOTIMPL             = unchecked((int)0x80004001);
        public const int E_INVALIDARG          = unchecked((int)0x80070057);
        public const int E_OUTOFMEMORY         = unchecked((int)0x8007000E);
        public const int OPC_E_INVALIDHANDLE   = unchecked((int)0xC0040001);
        public const int OPC_E_UNKNOWNITEMID   = unchecked((int)0xC0040007);
        public const int OPC_E_BADTYPE         = unchecked((int)0xC0040004);
        public const int OPC_S_CLAMP           = 0x00040002;
    }

    #region OPC DA Enums
    public enum OPCSERVERSTATE
    {
        OPC_STATUS_RUNNING = 1,
        OPC_STATUS_FAILED  = 2,
        OPC_STATUS_NOCONFIG = 3,
        OPC_STATUS_SUSPENDED = 4,
        OPC_STATUS_TEST = 5,
        OPC_STATUS_COMM_FAULT = 6
    }

    public enum OPCDATASOURCE
    {
        OPC_DS_CACHE  = 1,
        OPC_DS_DEVICE = 2
    }

    public enum OPCNAMESPACETYPE
    {
        OPC_NS_HIERARCHICAL = 1,
        OPC_NS_FLAT = 2
    }

    public enum OPCBROWSEDIRECTION
    {
        OPC_BROWSE_UP = 1,
        OPC_BROWSE_DOWN = 2,
        OPC_BROWSE_TO = 3
    }

    public enum OPCBROWSETYPE
    {
        OPC_BRANCH = 1,
        OPC_LEAF = 2,
        OPC_FLAT = 3
    }
    #endregion

    #region OPC DA Structs
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct OPCSERVERSTATUS
    {
        public System.Runtime.InteropServices.ComTypes.FILETIME ftStartTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftCurrentTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftLastUpdateTime;
        public OPCSERVERSTATE dwServerState;
        public int dwGroupCount;
        public int dwBandWidth;
        public short wMajorVersion;
        public short wMinorVersion;
        public short wBuildNumber;
        public short wReserved;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string szVendorInfo;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct OPCITEMDEF
    {
        [MarshalAs(UnmanagedType.LPWStr)]
        public string szAccessPath;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string szItemID;
        public int bActive;
        public int hClient;
        public int dwBlobSize;
        public IntPtr pBlob;
        public short vtRequestedDataType;
        public short wReserved;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct OPCITEMRESULT
    {
        public int hServer;
        public short vtCanonicalDataType;
        public short wReserved;
        public int dwAccessRights;
        public int dwBlobSize;
        public IntPtr pBlob;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct OPCITEMSTATE
    {
        public int hClient;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftTimeStamp;
        public short wQuality;
        public short wReserved;
        [MarshalAs(UnmanagedType.Struct)]
        public object vDataValue;
    }
    #endregion

    #region OPC DA COM Interfaces
    [ComImport]
    [Guid("00000001-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IClassFactory
    {
        [PreserveSig]
        int CreateInstance(IntPtr pUnkOuter, [In] ref Guid riid, out IntPtr ppvObject);
        [PreserveSig]
        int LockServer([MarshalAs(UnmanagedType.Bool)] bool fLock);
    }

    [ComImport]
    [Guid("39c13a4d-011e-11d0-9675-0020afd8adb3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCServer
    {
        [PreserveSig]
        int AddGroup(
            [MarshalAs(UnmanagedType.LPWStr)] string szName,
            [MarshalAs(UnmanagedType.Bool)] bool bActive,
            int dwRequestedUpdateRate,
            int hClientGroup,
            IntPtr pTimeBias,
            IntPtr pPercentDeadband,
            int dwLCID,
            out int phServerGroup,
            out int pRevisedUpdateRate,
            [In] ref Guid riid,
            [MarshalAs(UnmanagedType.IUnknown)] out object ppUnk);

        [PreserveSig]
        int GetErrorString(int dwError, int dwLocale, [MarshalAs(UnmanagedType.LPWStr)] out string ppString);

        [PreserveSig]
        int GetGroupByName([MarshalAs(UnmanagedType.LPWStr)] string szName, [In] ref Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object ppUnk);

        [PreserveSig]
        int GetStatus(out IntPtr ppServerStatus);

        [PreserveSig]
        int RemoveGroup(int hServerGroup, [MarshalAs(UnmanagedType.Bool)] bool bForce);

        [PreserveSig]
        int CreateGroupEnumerator(int dwScope, [In] ref Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object ppUnk);
    }

    [ComImport]
    [Guid("F31DFDE2-07B6-11d2-B2D8-0060083BA1FB")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCCommon
    {
        [PreserveSig]
        int SetLocaleID(int dwLcid);
        [PreserveSig]
        int GetLocaleID(out int pdwLcid);
        [PreserveSig]
        int QueryAvailableLocaleIDs(out int pdwCount, out IntPtr pdwLcid);
        [PreserveSig]
        int GetErrorString(int dwError, [MarshalAs(UnmanagedType.LPWStr)] out string ppString);
        [PreserveSig]
        int SetClientName([MarshalAs(UnmanagedType.LPWStr)] string szName);
    }

    [ComImport]
    [Guid("39c13a50-011e-11d0-9675-0020afd8adb3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCGroupStateMgt
    {
        [PreserveSig]
        int GetState(
            out int pUpdateRate,
            [MarshalAs(UnmanagedType.Bool)] out bool pActive,
            [MarshalAs(UnmanagedType.LPWStr)] out string ppName,
            out int pTimeBias,
            out float pPercentDeadband,
            out int pLCID,
            out int phClientGroup,
            out int phServerGroup);

        [PreserveSig]
        int SetState(
            IntPtr pRequestedUpdateRate,
            out int pRevisedUpdateRate,
            IntPtr pActive,
            IntPtr pTimeBias,
            IntPtr pPercentDeadband,
            IntPtr pLCID,
            IntPtr phClientGroup);

        [PreserveSig]
        int SetName([MarshalAs(UnmanagedType.LPWStr)] string szName);

        [PreserveSig]
        int CloneGroup([MarshalAs(UnmanagedType.LPWStr)] string szName, [In] ref Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object ppUnk);
    }

    [ComImport]
    [Guid("39c13a54-011e-11d0-9675-0020afd8adb3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCItemMgt
    {
        [PreserveSig]
        int AddItems(
            int dwCount,
            IntPtr pItemArray, // OPCITEMDEF[]
            out IntPtr ppAddResults, // OPCITEMRESULT[]
            out IntPtr ppErrors); // int[]

        [PreserveSig]
        int ValidateItems(
            int dwCount,
            IntPtr pItemArray,
            [MarshalAs(UnmanagedType.Bool)] bool bBlobUpdate,
            out IntPtr ppValidationResults,
            out IntPtr ppErrors);

        [PreserveSig]
        int RemoveItems(int dwCount, IntPtr phServer, out IntPtr ppErrors);

        [PreserveSig]
        int SetActiveState(int dwCount, IntPtr phServer, [MarshalAs(UnmanagedType.Bool)] bool bActive, out IntPtr ppErrors);

        [PreserveSig]
        int SetClientHandles(int dwCount, IntPtr phServer, IntPtr phClient, out IntPtr ppErrors);

        [PreserveSig]
        int SetDatatypes(int dwCount, IntPtr phServer, IntPtr pRequestedDatatypes, out IntPtr ppErrors);

        [PreserveSig]
        int CreateEnumerator([In] ref Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object ppUnk);
    }

    [ComImport]
    [Guid("39c13a52-011e-11d0-9675-0020afd8adb3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCSyncIO
    {
        [PreserveSig]
        int Read(
            OPCDATASOURCE dwSource,
            int dwCount,
            IntPtr phServer, // int[]
            out IntPtr ppItemValues, // OPCITEMSTATE[]
            out IntPtr ppErrors); // int[]

        [PreserveSig]
        int Write(
            int dwCount,
            IntPtr phServer, // int[]
            IntPtr pItemValues, // object/VARIANT[]
            out IntPtr ppErrors); // int[]
    }

    [ComImport]
    [Guid("39c13a71-011e-11d0-9675-0020afd8adb3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCAsyncIO2
    {
        [PreserveSig]
        int Read(
            int dwCount,
            IntPtr phServer,
            int dwTransactionID,
            out int pdwCancelID,
            out IntPtr ppErrors);

        [PreserveSig]
        int Write(
            int dwCount,
            IntPtr phServer,
            IntPtr pItemValues,
            int dwTransactionID,
            out int pdwCancelID,
            out IntPtr ppErrors);

        [PreserveSig]
        int Refresh2(
            OPCDATASOURCE dwSource,
            int dwTransactionID,
            out int pdwCancelID);

        [PreserveSig]
        int Cancel2(int dwCancelID);

        [PreserveSig]
        int SetEnable([MarshalAs(UnmanagedType.Bool)] bool bEnable);

        [PreserveSig]
        int GetEnable([MarshalAs(UnmanagedType.Bool)] out bool pbEnable);
    }

    [ComImport]
    [Guid("39c13a70-011e-11d0-9675-0020afd8adb3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCDataCallback
    {
        [PreserveSig]
        int OnDataChange(
            int dwTransid,
            int hGroup,
            int hrMasterquality,
            int hrMastererror,
            int dwCount,
            IntPtr phClientItems,
            IntPtr pvValues,
            IntPtr pwQualities,
            IntPtr pftTimeStamps,
            IntPtr pErrors);

        [PreserveSig]
        int OnReadComplete(
            int dwTransid,
            int hGroup,
            int hrMasterquality,
            int hrMastererror,
            int dwCount,
            IntPtr phClientItems,
            IntPtr pvValues,
            IntPtr pwQualities,
            IntPtr pftTimeStamps,
            IntPtr pErrors);

        [PreserveSig]
        int OnWriteComplete(
            int dwTransid,
            int hGroup,
            int hrMastererr,
            int dwCount,
            IntPtr pClienthandles,
            IntPtr pErrors);

        [PreserveSig]
        int OnCancelComplete(
            int dwTransid,
            int hGroup);
    }

    [ComImport]
    [Guid("00000101-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IEnumString
    {
        [PreserveSig]
        int Next(
            int celt,
            IntPtr rgelt,
            IntPtr pceltFetched);

        [PreserveSig]
        int Skip(int celt);

        [PreserveSig]
        int Reset();

        [PreserveSig]
        int Clone(out IEnumString ppenum);
    }

    [ComImport]
    [Guid("39C13A4F-011E-11D0-9675-0020AFD8ADB3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCBrowseServerAddressSpace
    {
        [PreserveSig]
        int QueryOrganization(out OPCNAMESPACETYPE pNameSpaceType);

        [PreserveSig]
        int ChangeBrowsePosition(OPCBROWSEDIRECTION dwBrowseDirection, [MarshalAs(UnmanagedType.LPWStr)] string szString);

        [PreserveSig]
        int BrowseOPCItemIDs(
            OPCBROWSETYPE dwBrowseFilterType,
            [MarshalAs(UnmanagedType.LPWStr)] string szFilterCriteria,
            short vtDataTypeFilter,
            int dwAccessRightsFilter,
            out IEnumString ppIEnumString);

        [PreserveSig]
        int GetItemID([MarshalAs(UnmanagedType.LPWStr)] string szItemDataID, [MarshalAs(UnmanagedType.LPWStr)] out string szItemID);

        [PreserveSig]
        int BrowseAccessPaths([MarshalAs(UnmanagedType.LPWStr)] string szItemID, out IEnumString ppIEnumString);
    }

    [ComImport]
    [Guid("39c13a72-011e-11d0-9675-0020afd8adb3")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IOPCItemProperties
    {
        [PreserveSig]
        int QueryAvailableProperties(
            [MarshalAs(UnmanagedType.LPWStr)] string szItemID,
            out int pdwCount,
            out IntPtr ppPropertyIDs,
            out IntPtr ppDescriptions,
            out IntPtr ppvtDataTypes);

        [PreserveSig]
        int GetItemProperties(
            [MarshalAs(UnmanagedType.LPWStr)] string szItemID,
            int dwCount,
            IntPtr pdwPropertyIDs,
            out IntPtr ppvData,
            out IntPtr ppErrors);

        [PreserveSig]
        int LookupItemIDs(
            [MarshalAs(UnmanagedType.LPWStr)] string szItemID,
            int dwCount,
            IntPtr pdwPropertyIDs,
            out IntPtr ppszNewItemIDs,
            out IntPtr ppErrors);
    }

    [ComImport]
    [Guid("B196B284-BAB4-101A-B69C-00AA00341D07")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IConnectionPointContainer
    {
        [PreserveSig]
        int EnumConnectionPoints(out IntPtr ppEnum);

        [PreserveSig]
        int FindConnectionPoint([In] ref Guid riid, out IConnectionPoint ppCP);
    }

    [ComImport]
    [Guid("B196B286-BAB4-101A-B69C-00AA00341D07")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IConnectionPoint
    {
        [PreserveSig]
        int GetConnectionInterface(out Guid pIID);

        [PreserveSig]
        int GetConnectionPointContainer(out IConnectionPointContainer ppCPC);

        [PreserveSig]
        int Advise([MarshalAs(UnmanagedType.IUnknown)] object pUnkSink, out int pdwCookie);

        [PreserveSig]
        int Unadvise(int dwCookie);

        [PreserveSig]
        int EnumConnections(out IntPtr ppEnum);
    }
    #endregion

    #region OPC Registry Helper
    public static class OpcRegistryHelper
    {
        [DllImport("oleaut32.dll")]
        public static extern int VariantClear(IntPtr pvarg);

        [DllImport("ole32.dll")]
        public static extern int CoInitializeSecurity(
            IntPtr pSecDesc,
            int cAuthSvc,
            IntPtr asAuthSvc,
            IntPtr pReserved1,
            uint dwAuthnLevel,
            uint dwImpLevel,
            IntPtr pAuthInfo,
            uint dwCapabilities,
            IntPtr pReserved3);

        public static void InitializeComSecurity()
        {
            try
            {
                // RPC_C_AUTHN_LEVEL_NONE = 1, RPC_C_IMP_LEVEL_IMPERSONATE = 3, EOAC_NONE = 0
                CoInitializeSecurity(
                    IntPtr.Zero,
                    -1,
                    IntPtr.Zero,
                    IntPtr.Zero,
                    1,
                    3,
                    IntPtr.Zero,
                    0,
                    IntPtr.Zero);
            }
            catch { }
        }

        [DllImport("ole32.dll")]
        public static extern int CoRegisterClassObject(
            [In] ref Guid rclsid,
            [MarshalAs(UnmanagedType.IUnknown)] object pUnk,
            int dwClsContext,
            int flags,
            out int lpdwRegister);

        [DllImport("ole32.dll")]
        public static extern int CoRevokeClassObject(int dwRegister);

        public const int CLSCTX_LOCAL_SERVER = 4;
        public const int CLSCTX_REMOTE_SERVER = 16;
        public const int REGCLS_MULTIPLEUSE = 1;

#if NETFRAMEWORK
        public static bool RegisterServer(string exePath, out string error)
        {
            error = "";
            try
            {
                if (string.IsNullOrEmpty(exePath) ||
                    exePath.EndsWith("powershell.exe", StringComparison.OrdinalIgnoreCase) ||
                    exePath.EndsWith("pwsh.exe", StringComparison.OrdinalIgnoreCase) ||
                    exePath.EndsWith("dotnet.exe", StringComparison.OrdinalIgnoreCase) ||
                    exePath.EndsWith("csc.exe", StringComparison.OrdinalIgnoreCase))
                {
                    string candidate = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "NetToCXSim.exe");
                    if (File.Exists(candidate))
                    {
                        exePath = candidate;
                    }
                    else
                    {
                        error = "Invalid executable host for COM registration.";
                        return false;
                    }
                }

                string clsidStr = "{" + OpcDaConstants.CLSID_NetToCxSimOpcDa.ToString() + "}";

                // 1. Register in HKCU (Always succeeds for current user)
                try
                {
                    using (var root = Registry.CurrentUser.OpenSubKey(@"Software\Classes", true))
                    {
                        if (root != null)
                        {
                            RegisterInClassesKey(root, clsidStr, exePath);
                        }
                    }
                }
                catch { }

                // 2. Register in HKLM 32-bit (HKLM\SOFTWARE\WOW6432Node\Classes on 64-bit Windows)
                // This is CRITICAL for 32-bit OpcEnum and 32-bit OPC Clients/SCADA
                try
                {
                    using (var hklm32 = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry32).OpenSubKey(@"Software\Classes", true))
                    {
                        if (hklm32 != null)
                        {
                            RegisterInClassesKey(hklm32, clsidStr, exePath);
                        }
                    }
                }
                catch { }

                // 3. Register in HKLM 64-bit (HKLM\SOFTWARE\Classes)
                // This is for 64-bit OPC Clients/SCADA and 64-bit Windows Explorer
                try
                {
                    using (var hklm64 = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(@"Software\Classes", true))
                    {
                        if (hklm64 != null)
                        {
                            RegisterInClassesKey(hklm64, clsidStr, exePath);
                        }
                    }
                }
                catch { }

                // 4. Try restarting OpcEnum service silently to flush server cache
                try
                {
                    var psi = new System.Diagnostics.ProcessStartInfo("net.exe", "stop opcenum")
                    {
                        CreateNoWindow = true,
                        UseShellExecute = false,
                        WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden
                    };
                    var p = System.Diagnostics.Process.Start(psi);
                    p?.WaitForExit(1000);
                }
                catch { }

                return true;
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }
        }

        private static void RegisterInClassesKey(RegistryKey classesKey, string clsidStr, string exePath)
        {
            // ProgID -> CLSID
            using (var progIdKey = classesKey.CreateSubKey(OpcDaConstants.ProgId))
            {
                progIdKey.SetValue("", OpcDaConstants.ServerDescription);
                using (var clsidKey = progIdKey.CreateSubKey("CLSID"))
                {
                    clsidKey.SetValue("", clsidStr);
                }
                using (var opcKey = progIdKey.CreateSubKey("OPC"))
                {
                    opcKey.SetValue("", "");
                }
            }

            // CLSID keys
            using (var clsidMain = classesKey.CreateSubKey($@"CLSID\{clsidStr}"))
            {
                clsidMain.SetValue("", OpcDaConstants.ServerDescription);
                clsidMain.SetValue("AppID", clsidStr);

                using (var progIdKey = clsidMain.CreateSubKey("ProgID"))
                {
                    progIdKey.SetValue("", OpcDaConstants.ProgId);
                }

                using (var localServerKey = clsidMain.CreateSubKey("LocalServer32"))
                {
                    localServerKey.SetValue("", $"\"{exePath}\"");
                }

                // OPC Category Registration
                using (var catKey = clsidMain.CreateSubKey(@"Implemented Categories\{63D5F432-CFE4-11d1-B2C8-0060083BA1FB}")) // OPC DA 2.0
                {
                    catKey.SetValue("", "");
                }
                using (var catKey1 = clsidMain.CreateSubKey(@"Implemented Categories\{63D5F430-CFE4-11d1-B2C8-0060083BA1FB}")) // OPC DA 1.0
                {
                    catKey1.SetValue("", "");
                }
                using (var catKey3 = clsidMain.CreateSubKey(@"Implemented Categories\{CC54E38A-DB8C-11d2-AB76-00805F77D1E1}")) // OPC DA 3.0
                {
                    catKey3.SetValue("", "");
                }
            }

            // AppID entry
            using (var appKey = classesKey.CreateSubKey($@"AppID\{clsidStr}"))
            {
                appKey.SetValue("", OpcDaConstants.ServerDescription);
                appKey.SetValue("AuthenticationLevel", 1, RegistryValueKind.DWord);
                appKey.SetValue("RunAs", "Interactive User");
            }

            string exeName = Path.GetFileName(exePath);
            if (!string.IsNullOrEmpty(exeName))
            {
                using (var exeAppKey = classesKey.CreateSubKey($@"AppID\{exeName}"))
                {
                    exeAppKey.SetValue("AppID", clsidStr);
                }
            }
        }

        public static bool UnregisterServer(out string error)
        {
            error = "";
            try
            {
                string clsidStr = "{" + OpcDaConstants.CLSID_NetToCxSimOpcDa.ToString() + "}";

                // 1. Unregister HKCU
                using (var root = Registry.CurrentUser.OpenSubKey(@"Software\Classes", true))
                {
                    if (root != null)
                    {
                        try { root.DeleteSubKeyTree(OpcDaConstants.ProgId, false); } catch { }
                        try { root.DeleteSubKeyTree($@"CLSID\{clsidStr}", false); } catch { }
                        try { root.DeleteSubKeyTree($@"AppID\{clsidStr}", false); } catch { }
                        try { root.DeleteSubKeyTree(@"AppID\NetToCXSim.exe", false); } catch { }
                    }
                }

                // 2. Unregister HKLM 32-bit
                try
                {
                    using (var hklm32 = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry32).OpenSubKey(@"Software\Classes", true))
                    {
                        if (hklm32 != null)
                        {
                            try { hklm32.DeleteSubKeyTree(OpcDaConstants.ProgId, false); } catch { }
                            try { hklm32.DeleteSubKeyTree($@"CLSID\{clsidStr}", false); } catch { }
                            try { hklm32.DeleteSubKeyTree($@"AppID\{clsidStr}", false); } catch { }
                            try { hklm32.DeleteSubKeyTree(@"AppID\NetToCXSim.exe", false); } catch { }
                        }
                    }
                }
                catch { }

                // 3. Unregister HKLM 64-bit
                try
                {
                    using (var hklm64 = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64).OpenSubKey(@"Software\Classes", true))
                    {
                        if (hklm64 != null)
                        {
                            try { hklm64.DeleteSubKeyTree(OpcDaConstants.ProgId, false); } catch { }
                            try { hklm64.DeleteSubKeyTree($@"CLSID\{clsidStr}", false); } catch { }
                            try { hklm64.DeleteSubKeyTree($@"AppID\{clsidStr}", false); } catch { }
                            try { hklm64.DeleteSubKeyTree(@"AppID\NetToCXSim.exe", false); } catch { }
                        }
                    }
                }
                catch { }

                return true;
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }
        }

        public static bool IsServerRegistered()
        {
            try
            {
                string clsidStr = "{" + OpcDaConstants.CLSID_NetToCxSimOpcDa.ToString() + "}";
                using (var k = Registry.CurrentUser.OpenSubKey($@"Software\Classes\CLSID\{clsidStr}"))
                {
                    if (k != null) return true;
                }
                using (var k = Registry.ClassesRoot.OpenSubKey($@"CLSID\{clsidStr}"))
                {
                    if (k != null) return true;
                }
            }
            catch { }
            return false;
        }
#else
        public static bool RegisterServer(string exePath, out string error) { error = "Not supported on netstandard2.0"; return false; }
        public static bool UnregisterServer(out string error) { error = "Not supported on netstandard2.0"; return false; }
        public static bool IsServerRegistered() => false;
#endif
    }
    #endregion
}
