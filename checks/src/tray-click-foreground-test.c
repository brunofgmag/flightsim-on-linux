#include <windows.h>
#include <shellapi.h>
#include <stdio.h>

#define TRAY_CALLBACK (WM_APP + 1)
#define ICON_ID 9

static HWND owner;
static BOOL clicked;
static BOOL foreground_after_click;

static LRESULT CALLBACK owner_proc( HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam )
{
    if (msg == TRAY_CALLBACK && LOWORD(lparam) == WM_RBUTTONDOWN)
    {
        foreground_after_click = SetForegroundWindow( hwnd );
        clicked = TRUE;
        return 0;
    }
    return DefWindowProcW( hwnd, msg, wparam, lparam );
}

static HWND find_icon_window( void )
{
    HWND tray, icon = FindWindowExW( NULL, NULL, L"__wine_tray_icon", NULL );

    if (icon) return icon;
    if (!(tray = FindWindowW( L"Shell_TrayWnd", NULL ))) return NULL;
    return FindWindowExW( tray, NULL, L"__wine_tray_icon", NULL );
}

static void pump_until_clicked( DWORD timeout )
{
    DWORD start = GetTickCount();
    MSG msg;

    while (!clicked && GetTickCount() - start < timeout)
    {
        while (PeekMessageW( &msg, NULL, 0, 0, PM_REMOVE ))
        {
            TranslateMessage( &msg );
            DispatchMessageW( &msg );
        }
        Sleep( 10 );
    }
}

int main( void )
{
    WNDCLASSW cls = { 0 };
    NOTIFYICONDATAW nid = { 0 };
    HWND icon_window;
    DWORD start;
    int status;

    cls.lpfnWndProc = owner_proc;
    cls.hInstance = GetModuleHandleW( NULL );
    cls.lpszClassName = L"tray-click-foreground-test";
    RegisterClassW( &cls );
    owner = CreateWindowExW( 0, cls.lpszClassName, L"tray-click-foreground-test", WS_POPUP, 0, 0, 1, 1,
                             NULL, NULL, cls.hInstance, NULL );

    nid.cbSize = sizeof(nid);
    nid.hWnd = owner;
    nid.uID = ICON_ID;
    nid.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE;
    nid.uCallbackMessage = TRAY_CALLBACK;
    nid.hIcon = LoadIconW( NULL, (LPCWSTR)IDI_APPLICATION );
    lstrcpyW( nid.szTip, L"tray-click-foreground-test" );
    if (!Shell_NotifyIconW( NIM_ADD, &nid ))
    {
        printf( "cannot test: NIM_ADD failed, so nothing here hosts tray icons\n" );
        DestroyWindow( owner );
        return 3;
    }

    start = GetTickCount();
    while (!(icon_window = find_icon_window()) && GetTickCount() - start < 5000) Sleep( 50 );
    if (!icon_window)
    {
        printf( "cannot test: no __wine_tray_icon window appeared for the icon\n" );
        Shell_NotifyIconW( NIM_DELETE, &nid );
        DestroyWindow( owner );
        return 3;
    }

    if (!SetForegroundWindow( owner ))
    {
        printf( "cannot test: the owner window could not take the foreground the first time\n" );
        Shell_NotifyIconW( NIM_DELETE, &nid );
        DestroyWindow( owner );
        return 3;
    }
    Sleep( 20 );
    if (!SetForegroundWindow( GetAncestor( icon_window, GA_ROOT ) ))
    {
        printf( "cannot test: explorer's tray window could not take the foreground\n" );
        Shell_NotifyIconW( NIM_DELETE, &nid );
        DestroyWindow( owner );
        return 3;
    }
    printf( "ok explorer holds the foreground before the click\n" );

    PostMessageW( icon_window, WM_RBUTTONDOWN, MK_RBUTTON, MAKELPARAM( 8, 8 ) );
    PostMessageW( icon_window, WM_RBUTTONUP, 0, MAKELPARAM( 8, 8 ) );
    pump_until_clicked( 5000 );

    if (!clicked)
    {
        printf( "FAIL the owner never got WM_RBUTTONDOWN from the icon\n" );
        status = 1;
    }
    else if (!foreground_after_click)
    {
        printf( "FAIL SetForegroundWindow on the owner failed after a right-click on its icon, so Electron shows no tray menu\n" );
        status = 1;
    }
    else
    {
        printf( "ok SetForegroundWindow on the owner succeeded after a right-click on its icon\n" );
        status = 0;
    }

    Shell_NotifyIconW( NIM_DELETE, &nid );
    DestroyWindow( owner );
    return status;
}
