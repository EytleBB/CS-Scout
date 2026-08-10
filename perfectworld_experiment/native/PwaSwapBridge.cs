using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

// Small, auditable x86 bridge to the swapData export shipped with the
// installed Perfect World Arena client. Protocol data is read from stdin so
// signatures and request material never appear in a process command line.
internal static class PwaSwapBridge
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int SwapDataDelegate(
        IntPtr input,
        UInt32 inputLength,
        IntPtr output,
        ref UInt32 outputLength);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    [return: MarshalAs(UnmanagedType.I1)]
    private delegate bool GetCurrentIngameParametersDelegate(IntPtr output);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr LoadLibrary(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    private static extern IntPtr GetProcAddress(IntPtr module, string name);

    [DllImport("kernel32.dll")]
    private static extern bool FreeLibrary(IntPtr module);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetDllDirectory(string path);

    private static int Main(string[] args)
    {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);

        bool queryCurrent = args.Length == 2 && args[0] == "--current";
        bool probe = args.Length == 2 && args[0] == "--probe";
        if (args.Length != 1 && !queryCurrent && !probe)
        {
            Console.Error.WriteLine("usage: PwaSwapBridge.exe [--current|--probe] <PvpAlive.dll>");
            return 2;
        }

        string dllPath = Path.GetFullPath(args[(queryCurrent || probe) ? 1 : 0]);
        string dllDirectory = Path.GetDirectoryName(dllPath);
        string payload = (queryCurrent || probe) ? "" : Console.In.ReadToEnd();
        if (!queryCurrent && !probe && String.IsNullOrEmpty(payload))
        {
            Console.Error.WriteLine("empty swapData payload");
            return 2;
        }

        if (!SetDllDirectory(dllDirectory))
        {
            Console.Error.WriteLine("failed to configure DLL directory");
            return 1;
        }

        IntPtr module = LoadLibrary(dllPath);
        if (module == IntPtr.Zero)
        {
            Console.Error.WriteLine("failed to load PvpAlive.dll: " + Marshal.GetLastWin32Error());
            return 1;
        }

        try
        {
            if (queryCurrent)
                return QueryCurrentIngameParameters(module);

            if (probe)
                return GetProcAddress(module, "swapData") == IntPtr.Zero ? 1 : 0;

            IntPtr export = GetProcAddress(module, "swapData");
            if (export == IntPtr.Zero)
            {
                Console.Error.WriteLine("PvpAlive.dll does not export swapData");
                return 1;
            }

            SwapDataDelegate swapData = (SwapDataDelegate)Marshal.GetDelegateForFunctionPointer(
                export, typeof(SwapDataDelegate));
            byte[] inputBytes = Encoding.UTF8.GetBytes(payload);
            IntPtr input = Marshal.AllocHGlobal(inputBytes.Length + 1);
            const int outputCapacity = 4096;
            IntPtr output = Marshal.AllocHGlobal(outputCapacity);
            try
            {
                Marshal.Copy(inputBytes, 0, input, inputBytes.Length);
                Marshal.WriteByte(input, inputBytes.Length, 0);
                for (int index = 0; index < outputCapacity; index++)
                    Marshal.WriteByte(output, index, 0);

                UInt32 outputLength = outputCapacity;
                int result = swapData(
                    input,
                    checked((UInt32)inputBytes.Length),
                    output,
                    ref outputLength);
                if (result == 0)
                {
                    Console.Error.WriteLine("swapData rejected the payload");
                    return 1;
                }
                if (outputLength == 0 || outputLength > outputCapacity)
                {
                    Console.Error.WriteLine("swapData returned an invalid output length");
                    return 1;
                }

                byte[] outputBytes = new byte[outputLength];
                Marshal.Copy(output, outputBytes, 0, checked((Int32)outputLength));
                string value = Encoding.UTF8.GetString(outputBytes).TrimEnd('\0', '\r', '\n');
                Console.Out.Write(value);
                return 0;
            }
            finally
            {
                Marshal.FreeHGlobal(input);
                Marshal.FreeHGlobal(output);
            }
        }
        finally
        {
            FreeLibrary(module);
        }
    }

    private static int QueryCurrentIngameParameters(IntPtr module)
    {
        IntPtr export = GetProcAddress(module, "getCurrentIngameParameters");
        if (export == IntPtr.Zero)
        {
            Console.Error.WriteLine("PvpAlive.dll does not export getCurrentIngameParameters");
            return 1;
        }

        GetCurrentIngameParametersDelegate query =
            (GetCurrentIngameParametersDelegate)Marshal.GetDelegateForFunctionPointer(
                export, typeof(GetCurrentIngameParametersDelegate));
        IntPtr output = Marshal.AllocHGlobal(8);
        try
        {
            Marshal.WriteInt64(output, 0L);
            if (!query(output))
                return 3;
            UInt64 value = unchecked((UInt64)Marshal.ReadInt64(output));
            Console.Out.Write(value.ToString());
            return 0;
        }
        finally
        {
            Marshal.FreeHGlobal(output);
        }
    }
}
