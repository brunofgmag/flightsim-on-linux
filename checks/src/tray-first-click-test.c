#include <windows.h>
#include <shellapi.h>
#include <stdio.h>

#define TRAY_CALLBACK (WM_APP + 2)
#define ICON_ID 11

static BOOL right_button_down;

static LRESULT CALLBACK owner_proc( HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam )
{
    if (msg == TRAY_CALLBACK && LOWORD(lparam) == WM_RBUTTONDOWN)
    {
        right_button_down = TRUE;
        return 0;
    }
    return DefWindowProcW( hwnd, msg, wparam, lparam );
}

int main( int argc, char **argv )
{
    WNDCLASSW cls = { 0 };
    NOTIFYICONDATAW nid = { 0 };
    DWORD timeout = argc > 1 ? strtoul( argv[1], NULL, 10 ) : 20000;
    DWORD start;
    HWND owner;
    MSG msg;

    cls.lpfnWndProc = owner_proc;
    cls.hInstance = GetModuleHandleW( NULL );
    cls.lpszClassName = L"tray-first-click-test";
    RegisterClassW( &cls );
    owner = CreateWindowExW( 0, cls.lpszClassName, L"tray-first-click-test", WS_POPUP, 0, 0, 1, 1,
                             NULL, NULL, cls.hInstance, NULL );

    nid.cbSize = sizeof(nid);
    nid.hWnd = owner;
    nid.uID = ICON_ID;
    nid.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE;
    nid.uCallbackMessage = TRAY_CALLBACK;
    nid.hIcon = LoadIconW( NULL, (LPCWSTR)IDI_APPLICATION );
    lstrcpyW( nid.szTip, L"tray-first-click-test" );
    if (!Shell_NotifyIconW( NIM_ADD, &nid ))
    {
        printf( "cannot test: NIM_ADD failed, so nothing here hosts tray icons\n" );
        DestroyWindow( owner );
        return 3;
    }
    printf( "icon added; waiting %lu ms for a right-click\n", timeout );
    fflush( stdout );

    start = GetTickCount();
    while (!right_button_down && GetTickCount() - start < timeout)
    {
        while (PeekMessageW( &msg, NULL, 0, 0, PM_REMOVE ))
        {
            TranslateMessage( &msg );
            DispatchMessageW( &msg );
        }
        Sleep( 10 );
    }

    Shell_NotifyIconW( NIM_DELETE, &nid );
    DestroyWindow( owner );
    if (!right_button_down)
    {
        printf( "FAIL the icon's owner got no WM_RBUTTONDOWN from the first right-click\n" );
        return 1;
    }
    printf( "ok the icon's owner got WM_RBUTTONDOWN from the first right-click\n" );
    return 0;
}
