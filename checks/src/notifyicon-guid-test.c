#include <windows.h>
#include <shellapi.h>
#include <stdio.h>

static const GUID icon_guid = { 0x5f3c2a71, 0x9b4e, 0x4d2a, { 0x8c, 0x11, 0x6e, 0x0d, 0x3b, 0x52, 0xa9, 0x47 } };

static int failures;

static void check( const char *what, BOOL ok )
{
    printf( "%s %s\n", ok ? "ok" : "FAIL", what );
    if (!ok) failures++;
}

static NOTIFYICONDATAW guid_only( void )
{
    NOTIFYICONDATAW nid = { 0 };
    nid.cbSize = sizeof(nid);
    nid.uFlags = NIF_GUID;
    nid.guidItem = icon_guid;
    return nid;
}

int main( void )
{
    HWND owner = CreateWindowExW( 0, L"STATIC", L"notifyicon-guid-test", 0, 0, 0, 0, 0,
                                  HWND_MESSAGE, NULL, GetModuleHandleW( NULL ), NULL );
    NOTIFYICONDATAW add = { 0 };
    NOTIFYICONDATAW version = guid_only();
    NOTIFYICONDATAW modify = guid_only();
    NOTIFYICONDATAW remove = guid_only();

    add.cbSize = sizeof(add);
    add.hWnd = owner;
    add.uID = 7;
    add.uFlags = NIF_GUID | NIF_ICON | NIF_TIP | NIF_MESSAGE;
    add.uCallbackMessage = WM_APP;
    add.hIcon = LoadIconW( NULL, (LPCWSTR)IDI_APPLICATION );
    add.guidItem = icon_guid;
    lstrcpyW( add.szTip, L"notifyicon-guid-test" );
    if (!Shell_NotifyIconW( NIM_ADD, &add ))
    {
        printf( "cannot test: NIM_ADD with window and GUID failed, so nothing here hosts tray icons\n" );
        DestroyWindow( owner );
        return 3;
    }
    check( "NIM_ADD with window and GUID", TRUE );

    version.uVersion = NOTIFYICON_VERSION_4;
    check( "NIM_SETVERSION 4 by GUID only", Shell_NotifyIconW( NIM_SETVERSION, &version ) );

    modify.uFlags |= NIF_TIP;
    lstrcpyW( modify.szTip, L"notifyicon-guid-test modified" );
    check( "NIM_MODIFY by GUID only", Shell_NotifyIconW( NIM_MODIFY, &modify ) );

    check( "NIM_DELETE by GUID only", Shell_NotifyIconW( NIM_DELETE, &remove ) );
    check( "second NIM_DELETE fails", !Shell_NotifyIconW( NIM_DELETE, &remove ) );

    DestroyWindow( owner );
    return failures ? 1 : 0;
}
