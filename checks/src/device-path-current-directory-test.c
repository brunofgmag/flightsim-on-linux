#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define PATH_CHARS 512
#define DEVICE_PREFIX L"\\Device\\HarddiskVolume"
#define DEVICE_ROOT L"\\\\.\\GLOBALROOT"

static int device_directory(WCHAR *out)
{
    WCHAR nt_name[PATH_CHARS];
    HANDLE handle = CreateFileW(L"C:\\windows", FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS, NULL);
    DWORD len;

    if (handle == INVALID_HANDLE_VALUE)
    {
        printf("could not open C:\\windows, error %lu\n", GetLastError());
        return 2;
    }
    len = GetFinalPathNameByHandleW(handle, nt_name, PATH_CHARS, VOLUME_NAME_NT);
    CloseHandle(handle);
    if (!len || len >= PATH_CHARS || wcsncmp(nt_name, DEVICE_PREFIX, ARRAYSIZE(DEVICE_PREFIX) - 1))
    {
        printf("GetFinalPathNameByHandleW(VOLUME_NAME_NT) of C:\\windows gave %ls, which is not a \\Device\\HarddiskVolumeN path\n",
                len && len < PATH_CHARS ? nt_name : L"nothing");
        return 2;
    }
    wcscpy(out, DEVICE_ROOT);
    wcscat(out, nt_name);
    return 0;
}

static int check_current_directory(const char *who, const WCHAR *directory)
{
    WCHAR current[PATH_CHARS], full[PATH_CHARS], expected[PATH_CHARS];
    DWORD len = GetCurrentDirectoryW(PATH_CHARS, current);

    if (!len || len >= PATH_CHARS || wcscmp(current, directory))
    {
        printf("%s: GetCurrentDirectoryW returned %ls, Windows returns %ls FAIL\n", who,
                len && len < PATH_CHARS ? current : L"nothing", directory);
        return 1;
    }
    printf("%s: GetCurrentDirectoryW returned %ls ok\n", who, current);

    wcscpy(expected, directory);
    wcscat(expected, L"\\win.ini");
    len = GetFullPathNameW(L"win.ini", PATH_CHARS, full, NULL);
    if (!len || len >= PATH_CHARS || wcscmp(full, expected))
    {
        printf("%s: GetFullPathNameW(win.ini) returned %ls, Windows returns %ls FAIL\n", who,
                len && len < PATH_CHARS ? full : L"nothing", expected);
        return 1;
    }
    printf("%s: GetFullPathNameW(win.ini) returned %ls ok\n", who, full);

    if (GetFileAttributesW(L"win.ini") == INVALID_FILE_ATTRIBUTES)
    {
        printf("%s: GetFileAttributesW(win.ini) failed with error %lu, Windows finds the file FAIL\n", who,
                GetLastError());
        return 1;
    }
    printf("%s: GetFileAttributesW(win.ini) found the file ok\n", who);
    return 0;
}

static int run_child(const WCHAR *directory)
{
    WCHAR program[PATH_CHARS], command_line[PATH_CHARS + 16];
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process;
    DWORD code = 1;

    startup.cb = sizeof(startup);
    GetModuleFileNameW(NULL, program, PATH_CHARS);
    wcscpy(command_line, L"\"");
    wcscat(command_line, program);
    wcscat(command_line, L"\" child");
    fflush(stdout);
    if (!CreateProcessW(NULL, command_line, NULL, NULL, TRUE, 0, NULL, directory, &startup, &process))
    {
        printf("CreateProcessW with lpCurrentDirectory %ls failed with error %lu, Windows starts it FAIL\n",
                directory, GetLastError());
        return 1;
    }
    WaitForSingleObject(process.hProcess, 30000);
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    if (code)
    {
        printf("the child started with lpCurrentDirectory %ls exited with code %lu FAIL\n", directory, code);
        return 1;
    }
    printf("the child started with lpCurrentDirectory %ls kept it ok\n", directory);
    return 0;
}

int main(int argc, char **argv)
{
    WCHAR directory[PATH_CHARS];
    int status = device_directory(directory);

    if (status) return status;
    if (argc > 1 && !strcmp(argv[1], "child")) return check_current_directory("child", directory);

    if (!SetCurrentDirectoryW(directory))
    {
        printf("SetCurrentDirectoryW(%ls) failed with error %lu, Windows accepts it FAIL\n", directory, GetLastError());
        return 1;
    }
    if (check_current_directory("parent", directory)) return 1;
    return run_child(directory);
}
