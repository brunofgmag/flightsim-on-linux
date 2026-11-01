#include <windows.h>
#include <shellapi.h>
#include <wchar.h>
#include <stdlib.h>

static int first_argument(WCHAR **argv, int argc)
{
    const WCHAR *name = argv[0], *slash = wcsrchr(name, L'\\');

    if (slash) name = slash + 1;
    if (argc && !_wcsicmp(name, L"com-environment.exe")) return 1;
    if (argc && !_wcsicmp(name, L"com-environment")) return 1;
    return 0;
}

static WCHAR *append_quoted(WCHAR *out, const WCHAR *arg)
{
    *out++ = L'"';
    while (*arg) *out++ = *arg++;
    *out++ = L'"';
    *out++ = L' ';
    return out;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, WCHAR *raw, int show)
{
    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi;
    WCHAR *command, *out, *equals, directory[MAX_PATH], *slash;
    DWORD exit_code = 1;
    size_t length = 0;
    WCHAR **argv;
    int argc, first, i;

    if (!(argv = CommandLineToArgvW(GetCommandLineW(), &argc))) return 2;
    first = first_argument(argv, argc);
    if (argc - first < 2 || !(equals = wcschr(argv[first], L'='))) return 2;

    *equals = 0;
    if (!SetEnvironmentVariableW(argv[first], equals + 1)) return 3;

    for (i = first + 1; i < argc; i++) length += wcslen(argv[i]) + 3;
    if (!(command = calloc(length + 1, sizeof(WCHAR)))) return 4;
    for (out = command, i = first + 1; i < argc; i++) out = append_quoted(out, argv[i]);
    out[-1] = 0;

    lstrcpynW(directory, argv[first + 1], MAX_PATH);
    if ((slash = wcsrchr(directory, L'\\'))) *slash = 0;

    if (!CreateProcessW(argv[first + 1], command, NULL, NULL, FALSE, 0, NULL, slash ? directory : NULL, &si, &pi)) return 5;
    CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    return exit_code;
}
