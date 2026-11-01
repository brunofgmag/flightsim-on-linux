#include <windows.h>

typedef BOOL (WINAPI *add_request_headers_fn)(void *, const WCHAR *, DWORD, DWORD);

BOOL WINAPI WinHttpAddRequestHeaders(void *request, const WCHAR *headers, DWORD length, DWORD flags)
{
    static add_request_headers_fn wine_add_request_headers;

    if (headers && length)
    {
        DWORD total = length == ~0u ? (DWORD)lstrlenW(headers) : length;
        DWORD skipped = 0;

        while (skipped < total && (headers[skipped] == '\r' || headers[skipped] == '\n'))
            skipped++;
        if (skipped == total)
            return TRUE;
        headers += skipped;
        length = total - skipped;
    }

    if (!wine_add_request_headers)
        wine_add_request_headers = (add_request_headers_fn)GetProcAddress(LoadLibraryW(L"winhttp_wine.dll"), "WinHttpAddRequestHeaders");
    return wine_add_request_headers(request, headers, length, flags);
}
