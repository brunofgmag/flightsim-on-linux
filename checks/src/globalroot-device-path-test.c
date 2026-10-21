#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#define PATH_CHARS 512
#define DEVICE_PREFIX L"\\Device\\HarddiskVolume"

static HANDLE open_for_info(const WCHAR *path, DWORD access, BY_HANDLE_FILE_INFORMATION *info)
{
    HANDLE handle = CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    if (handle != INVALID_HANDLE_VALUE && !GetFileInformationByHandle(handle, info))
    {
        CloseHandle(handle);
        return INVALID_HANDLE_VALUE;
    }
    return handle;
}

static BOOL same_file(const BY_HANDLE_FILE_INFORMATION *a, const BY_HANDLE_FILE_INFORMATION *b)
{
    return a->dwVolumeSerialNumber == b->dwVolumeSerialNumber && a->nFileIndexHigh == b->nFileIndexHigh
            && a->nFileIndexLow == b->nFileIndexLow;
}

static int check_same_file(const WCHAR *path, const WCHAR *reference_name, const BY_HANDLE_FILE_INFORMATION *reference)
{
    BY_HANDLE_FILE_INFORMATION info;
    HANDLE handle = open_for_info(path, FILE_READ_ATTRIBUTES, &info);

    if (handle == INVALID_HANDLE_VALUE)
    {
        printf("CreateFileW of %ls failed with error %lu, Windows opens it FAIL\n", path, GetLastError());
        return 1;
    }
    CloseHandle(handle);
    if (!same_file(&info, reference))
    {
        printf("CreateFileW of %ls opened a file that is not %ls FAIL\n", path, reference_name);
        return 1;
    }
    printf("CreateFileW of %ls opened %ls ok\n", path, reference_name);
    return 0;
}

static int check_final_name(const WCHAR *path, const WCHAR *expected)
{
    WCHAR final_name[PATH_CHARS];
    HANDLE handle = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD len;

    if (handle == INVALID_HANDLE_VALUE)
    {
        printf("CreateFileW of %ls failed with error %lu, Windows opens it FAIL\n", path, GetLastError());
        return 1;
    }
    len = GetFinalPathNameByHandleW(handle, final_name, PATH_CHARS, VOLUME_NAME_NT);
    CloseHandle(handle);
    if (!len || len >= PATH_CHARS || wcscmp(final_name, expected))
    {
        printf("the NT path of the handle opened through %ls is %ls, Windows returns %ls FAIL\n", path,
                len && len < PATH_CHARS ? final_name : L"nothing", expected);
        return 1;
    }
    printf("the NT path of the handle opened through %ls is %ls ok\n", path, final_name);
    return 0;
}

static int windows_directory_nt_name(WCHAR *nt_name, BY_HANDLE_FILE_INFORMATION *info)
{
    HANDLE directory = open_for_info(L"C:\\windows", FILE_READ_ATTRIBUTES, info);
    DWORD len;

    if (directory == INVALID_HANDLE_VALUE)
    {
        printf("could not open C:\\windows, error %lu\n", GetLastError());
        return 2;
    }
    len = GetFinalPathNameByHandleW(directory, nt_name, PATH_CHARS, VOLUME_NAME_NT);
    CloseHandle(directory);
    if (!len || len >= PATH_CHARS || wcsncmp(nt_name, DEVICE_PREFIX, ARRAYSIZE(DEVICE_PREFIX) - 1))
    {
        printf("GetFinalPathNameByHandleW(VOLUME_NAME_NT) of C:\\windows gave %ls, which is not a \\Device\\HarddiskVolumeN path\n",
                len && len < PATH_CHARS ? nt_name : L"nothing");
        return 2;
    }
    printf("the NT path of C:\\windows is %ls\n", nt_name);
    return 0;
}

int main(void)
{
    static const WCHAR *const roots[] = {L"\\\\?\\GLOBALROOT", L"\\\\.\\GLOBALROOT"};
    BY_HANDLE_FILE_INFORMATION directory_info;
    WCHAR directory_nt[PATH_CHARS], path[PATH_CHARS], expected[PATH_CHARS];
    size_t i;
    int status = windows_directory_nt_name(directory_nt, &directory_info);

    if (status) return status;
    for (i = 0; i < ARRAYSIZE(roots); i++)
    {
        wcscpy(path, roots[i]);
        wcscat(path, directory_nt);
        if (check_same_file(path, L"C:\\windows", &directory_info)) return 1;
    }

    wcscpy(path, roots[0]);
    wcscat(path, directory_nt);
    wcscat(path, L"\\win.ini");
    wcscpy(expected, directory_nt);
    wcscat(expected, L"\\win.ini");
    return check_final_name(path, expected);
}
