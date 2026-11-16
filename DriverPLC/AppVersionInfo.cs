using System;
using System.Reflection;

namespace NetToCXSim.Services
{
    public static class AppVersionInfo
    {
        private static readonly Version _version = typeof(AppVersionInfo).Assembly.GetName().Version;

        public static Version Version => _version;
        public static string VersionString => $"{_version.Major}.{_version.Minor}.{_version.Build}";
        public static int Major => _version.Major;
        public static int Minor => _version.Minor;
        public static int Build => _version.Build;
    }
}
