#include <windows.h>
#include <shellapi.h>
#include <stdio.h>

static const GUID icon_guid = { 0x2b7d4c19, 0x6e3a, 0x4f58, { 0x9a, 0x02, 0x51, 0xc7, 0x8e, 0x34, 0xd6, 0x1b } };

static int failures;

static void check( const char *what, BOOL ok )
{
    printf( "%s %s\n", ok ? "ok" : "FAIL", what );
    if (!ok) failures++;
}

static HRESULT get_rect( HWND owner, UINT id, const GUID *guid, RECT *rect )
{
    NOTIFYICONIDENTIFIER identifier = { 0 };

    identifier.cbSize = sizeof(identifier);
    identifier.hWnd = owner;
    identifier.uID = id;
    if (guid) identifier.guidItem = *guid;
    return Shell_NotifyIconGetRect( &identifier, rect );
}

int main( void )
{
    HWND owner = CreateWindowExW( 0, L"STATIC", L"notifyicon-get-rect-test", 0, 0, 0, 0, 0,
                                  HWND_MESSAGE, NULL, GetModuleHandleW( NULL ), NULL );
    NOTIFYICONDATAW add = { 0 };
    NOTIFYICONDATAW remove = { 0 };
    RECT rect = { 0 };
    HRESULT hr = E_FAIL;
    DWORD start;

    add.cbSize = sizeof(add);
    add.hWnd = owner;
    add.uID = 5;
    add.uFlags = NIF_GUID | NIF_ICON | NIF_TIP | NIF_MESSAGE;
    add.uCallbackMessage = WM_APP;
    add.hIcon = LoadIconW( NULL, (LPCWSTR)IDI_APPLICATION );
    add.guidItem = icon_guid;
    lstrcpyW( add.szTip, L"notifyicon-get-rect-test" );
    if (!Shell_NotifyIconW( NIM_ADD, &add ))
    {
        printf( "cannot test: NIM_ADD failed, so nothing here hosts tray icons\n" );
        DestroyWindow( owner );
        return 3;
    }

    start = GetTickCount();
    while ((hr = get_rect( owner, 5, NULL, &rect )) != S_OK && GetTickCount() - start < 5000) Sleep( 50 );
    printf( "Shell_NotifyIconGetRect by window and id returned 0x%08lx, rect (%ld,%ld)-(%ld,%ld)\n",
            hr, rect.left, rect.top, rect.right, rect.bottom );
    check( "Shell_NotifyIconGetRect by window and id", hr == S_OK && rect.right > rect.left && rect.bottom > rect.top );

    hr = get_rect( NULL, 0, &icon_guid, &rect );
    check( "Shell_NotifyIconGetRect by GUID alone", hr == S_OK && rect.right > rect.left && rect.bottom > rect.top );

    check( "Shell_NotifyIconGetRect fails for an icon that does not exist", get_rect( owner, 6, NULL, &rect ) != S_OK );

    remove.cbSize = sizeof(remove);
    remove.hWnd = owner;
    remove.uID = 5;
    remove.uFlags = NIF_GUID;
    remove.guidItem = icon_guid;
    Shell_NotifyIconW( NIM_DELETE, &remove );
    start = GetTickCount();
    while (get_rect( owner, 5, NULL, &rect ) == S_OK && GetTickCount() - start < 5000) Sleep( 50 );
    check( "Shell_NotifyIconGetRect fails once the icon is deleted", get_rect( owner, 5, NULL, &rect ) != S_OK );

    DestroyWindow( owner );
    return failures ? 1 : 0;
}
