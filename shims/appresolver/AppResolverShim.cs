using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace AppResolverShim
{
    [ComVisible(true)]
    [Guid("DE25675A-72DE-44b4-9373-05170450C140")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IApplicationResolver
    {
        void GetAppIDForShortcut(
            IntPtr psi,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszAppID);

        void GetAppIDForShortcutObject(
            IntPtr psl,
            IntPtr psi,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszAppID);

        void GetAppIDForWindow(
            int hwnd,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszAppID,
            [Out] out bool pfPinningPrevented,
            [Out] out bool pfExplicitAppID,
            [Out] out bool pfEmbeddedShortcutValid);

        void GetAppIDForProcess(
            uint dwProcessID,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszAppID,
            [Out] out bool pfPinningPrevented,
            [Out] out bool pfExplicitAppID,
            [Out] out bool pfEmbeddedShortcutValid);

        void GetShortcutForProcess(
            uint dwProcessID,
            [Out] out IntPtr ppsi);

        void GetBestShortcutForAppID(
            [MarshalAs(UnmanagedType.LPWStr)] string pszAppID,
            [Out] out IntPtr ppsi);

        void GetBestShortcutAndAppIDForAppPath(
            [MarshalAs(UnmanagedType.LPWStr)] string pszAppPath,
            [Out] out IntPtr ppsi,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszAppID);

        void CanPinApp(IntPtr psi);

        void CanPinAppShortcut(
            IntPtr psl,
            IntPtr psi);

        void GetRelaunchProperties(
            int hwnd,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszAppID,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszCmdLine,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszIconResource,
            [Out, MarshalAs(UnmanagedType.LPWStr)] out string ppszDisplayNameResource,
            [Out] bool pfPinnable);

        void GenerateShortcutFromWindowProperties(
            int hwnd,
            [Out] out IntPtr ppsi);

        void GenerateShortcutFromItemProperties(
            IntPtr psi2,
            [Out] out IntPtr ppsi);
    }

    [ComVisible(true)]
    [Guid("660b90c8-73a9-4b58-8cae-355b7f55341b")]
    [ClassInterface(ClassInterfaceType.None)]
    public class AppResolver : IApplicationResolver
    {
        public void GetAppIDForProcess(
            uint dwProcessID,
            out string ppszAppID,
            out bool pfPinningPrevented,
            out bool pfExplicitAppID,
            out bool pfEmbeddedShortcutValid)
        {
            string name = "Parallel42.App";
            try
            {
                string pn = Process.GetProcessById((int)dwProcessID).ProcessName;
                if (!string.IsNullOrWhiteSpace(pn))
                {
                    name = pn;
                }
            }
            catch
            {
            }

            ppszAppID = name;
            pfPinningPrevented = false;
            pfExplicitAppID = false;
            pfEmbeddedShortcutValid = false;
        }

        public void GetAppIDForShortcut(IntPtr psi, out string ppszAppID)
        {
            throw new NotImplementedException();
        }

        public void GetAppIDForShortcutObject(IntPtr psl, IntPtr psi, out string ppszAppID)
        {
            throw new NotImplementedException();
        }

        public void GetAppIDForWindow(int hwnd, out string ppszAppID, out bool pfPinningPrevented, out bool pfExplicitAppID, out bool pfEmbeddedShortcutValid)
        {
            throw new NotImplementedException();
        }

        public void GetShortcutForProcess(uint dwProcessID, out IntPtr ppsi)
        {
            throw new NotImplementedException();
        }

        public void GetBestShortcutForAppID(string pszAppID, out IntPtr ppsi)
        {
            throw new NotImplementedException();
        }

        public void GetBestShortcutAndAppIDForAppPath(string pszAppPath, out IntPtr ppsi, out string ppszAppID)
        {
            throw new NotImplementedException();
        }

        public void CanPinApp(IntPtr psi)
        {
            throw new NotImplementedException();
        }

        public void CanPinAppShortcut(IntPtr psl, IntPtr psi)
        {
            throw new NotImplementedException();
        }

        public void GetRelaunchProperties(int hwnd, out string ppszAppID, out string ppszCmdLine, out string ppszIconResource, out string ppszDisplayNameResource, bool pfPinnable)
        {
            throw new NotImplementedException();
        }

        public void GenerateShortcutFromWindowProperties(int hwnd, out IntPtr ppsi)
        {
            throw new NotImplementedException();
        }

        public void GenerateShortcutFromItemProperties(IntPtr psi2, out IntPtr ppsi)
        {
            throw new NotImplementedException();
        }
    }
}
