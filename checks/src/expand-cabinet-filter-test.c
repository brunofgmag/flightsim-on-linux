#include <windows.h>
#include <fcntl.h>
#include <fci.h>
#include <io.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

static const char kept_text[] = "the file the filter asks for\n";
static const char skipped_text[] = "a file the filter leaves in the cabinet\n";

static void *DIAMONDAPI fci_alloc(ULONG size) { return HeapAlloc(GetProcessHeap(), 0, size); }
static void DIAMONDAPI fci_free(void *ptr) { HeapFree(GetProcessHeap(), 0, ptr); }

static INT_PTR DIAMONDAPI fci_open(char *name, int flags, int mode, int *err, void *context)
{
    int fd = _open(name, flags | _O_BINARY, mode);
    if (fd == -1) *err = errno;
    return fd;
}

static UINT DIAMONDAPI fci_read(INT_PTR fd, void *buf, UINT size, int *err, void *context)
{
    int n = _read((int)fd, buf, size);
    if (n == -1) *err = errno;
    return n;
}

static UINT DIAMONDAPI fci_write(INT_PTR fd, void *buf, UINT size, int *err, void *context)
{
    int n = _write((int)fd, buf, size);
    if (n == -1) *err = errno;
    return n;
}

static int DIAMONDAPI fci_close(INT_PTR fd, int *err, void *context) { return _close((int)fd); }
static LONG DIAMONDAPI fci_seek(INT_PTR fd, LONG dist, int type, int *err, void *context) { return _lseek((int)fd, dist, type); }
static int DIAMONDAPI fci_delete(char *name, int *err, void *context) { return DeleteFileA(name) ? 0 : -1; }
static int DIAMONDAPI fci_placed(PCCAB cab, char *name, LONG size, BOOL continuation, void *context) { return 0; }
static BOOL DIAMONDAPI fci_next_cabinet(PCCAB cab, ULONG size, void *context) { return TRUE; }
static LONG DIAMONDAPI fci_status(UINT type, ULONG a, ULONG b, void *context) { return 0; }

static BOOL DIAMONDAPI fci_temp_file(char *name, int size, void *context)
{
    char dir[MAX_PATH];
    GetTempPathA(sizeof(dir), dir);
    return GetTempFileNameA(dir, "cab", 0, name) && DeleteFileA(name);
}

static INT_PTR DIAMONDAPI fci_open_info(char *name, USHORT *date, USHORT *time, USHORT *attributes, int *err, void *context)
{
    FILETIME filetime;
    HANDLE file = CreateFileA(name, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) return -1;
    GetFileTime(file, NULL, NULL, &filetime);
    FileTimeToDosDateTime(&filetime, date, time);
    CloseHandle(file);
    *attributes = FILE_ATTRIBUTE_NORMAL;
    return fci_open(name, _O_RDONLY, 0, err, context);
}

static BOOL write_file(const char *path, const char *text)
{
    DWORD written;
    HANDLE file = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    BOOL ok;
    if (file == INVALID_HANDLE_VALUE) return FALSE;
    ok = WriteFile(file, text, (DWORD)strlen(text), &written, NULL) && written == strlen(text);
    CloseHandle(file);
    return ok;
}

static BOOL make_cabinet(const char *dir, const char *cab_name)
{
    char kept[MAX_PATH], skipped[MAX_PATH];
    CCAB cab = {0};
    ERF erf;
    HFCI fci;
    BOOL ok;

    snprintf(kept, sizeof(kept), "%s\\kept.dll", dir);
    snprintf(skipped, sizeof(skipped), "%s\\skipped.txt", dir);
    if (!write_file(kept, kept_text) || !write_file(skipped, skipped_text)) return FALSE;

    cab.cb = 0x7fffffff;
    cab.cbFolderThresh = 0x7fffffff;
    snprintf(cab.szCabPath, sizeof(cab.szCabPath), "%s\\", dir);
    snprintf(cab.szCab, sizeof(cab.szCab), "%s", cab_name);
    fci = FCICreate(&erf, fci_placed, fci_alloc, fci_free, fci_open, fci_read, fci_write, fci_close, fci_seek,
            fci_delete, fci_temp_file, &cab, NULL);
    if (!fci) return FALSE;
    ok = FCIAddFile(fci, skipped, "skipped.txt", FALSE, fci_next_cabinet, fci_status, fci_open_info, tcompTYPE_MSZIP)
            && FCIAddFile(fci, kept, "kept.dll", FALSE, fci_next_cabinet, fci_status, fci_open_info, tcompTYPE_MSZIP)
            && FCIFlushCabinet(fci, FALSE, fci_next_cabinet, fci_status);
    FCIDestroy(fci);
    DeleteFileA(kept);
    DeleteFileA(skipped);
    return ok;
}

static DWORD run(char *command)
{
    STARTUPINFOA si = {sizeof(si)};
    PROCESS_INFORMATION pi;
    DWORD code = (DWORD)-1;

    if (!CreateProcessA(NULL, command, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) return code;
    WaitForSingleObject(pi.hProcess, 60000);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return code;
}

static BOOL file_has(const char *path, const char *text)
{
    char buffer[256] = {0};
    DWORD read = 0;
    HANDLE file = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) return FALSE;
    ReadFile(file, buffer, sizeof(buffer) - 1, &read, NULL);
    CloseHandle(file);
    return read == strlen(text) && !memcmp(buffer, text, read);
}

int main(void)
{
    char temp[MAX_PATH], dir[MAX_PATH], out[MAX_PATH], command[3 * MAX_PATH], path[MAX_PATH];
    HANDLE held;
    DWORD code;
    BOOL kept, skipped;

    GetTempPathA(sizeof(temp), temp);
    snprintf(dir, sizeof(dir), "%sexpand-filter-%lu", temp, GetCurrentProcessId());
    snprintf(out, sizeof(out), "%s\\out", dir);
    if (!CreateDirectoryA(dir, NULL) || !CreateDirectoryA(out, NULL) || !make_cabinet(dir, "model.cab"))
    {
        printf("could not build the test cabinet in %s\n", dir);
        return 2;
    }

    snprintf(path, sizeof(path), "%s\\model.cab", dir);
    held = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (held == INVALID_HANDLE_VALUE)
    {
        printf("could not open the test cabinet for reading\n");
        return 2;
    }
    snprintf(command, sizeof(command), "expand.exe -R -F:kept.dll \"%s\\model.cab\" \"%s\"", dir, out);
    code = run(command);
    CloseHandle(held);
    snprintf(path, sizeof(path), "%s\\kept.dll", out);
    kept = file_has(path, kept_text);
    DeleteFileA(path);
    snprintf(path, sizeof(path), "%s\\skipped.txt", out);
    skipped = GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
    DeleteFileA(path);
    snprintf(path, sizeof(path), "%s\\model.cab", dir);
    DeleteFileA(path);
    RemoveDirectoryA(out);
    RemoveDirectoryA(dir);

    if (code || !kept || skipped)
    {
        printf("expand.exe -R -F:kept.dll <cabinet> <folder>, with the cabinet held open for reading, exited %lu, "
                "%s kept.dll and %s skipped.txt; Windows exits 0 with only kept.dll in the folder FAIL\n",
                (unsigned long)code, kept ? "extracted" : "did not extract", skipped ? "also extracted" : "left out");
        return 1;
    }
    printf("expand.exe -R -F:kept.dll extracted only kept.dll from a two-file cabinet held open for reading ok\n");
    return 0;
}
