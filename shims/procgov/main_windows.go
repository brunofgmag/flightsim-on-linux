package main

import (
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const (
	unixShell    = "/bin/sh"
	statusPoll   = 50 * time.Millisecond
	startTimeout = 5 * time.Second
)

var (
	kernel32         = syscall.NewLazyDLL("kernel32.dll")
	wineUnixFileName = kernel32.NewProc("wine_get_unix_file_name")
	getProcessHeap   = kernel32.NewProc("GetProcessHeap")
	heapFree         = kernel32.NewProc("HeapFree")
)

func main() {
	args := os.Args[1:]
	target := indexOfExecutable(args)
	if target < 0 {
		os.Exit(1)
	}
	exe, rest := args[target], args[target+1:]
	selfDir, _ := filepath.Abs(filepath.Dir(os.Args[0]))

	if strings.EqualFold(filepath.Base(exe), "7za.exe") {
		if code, ok := runNativeSevenZip(rest); ok {
			os.Exit(code)
		}
		exe = filepath.Join(selfDir, "7za.exe")
	} else if !strings.ContainsAny(exe, `\/`) {
		if candidate := filepath.Join(selfDir, exe); fileExists(candidate) {
			exe = candidate
		}
	}
	os.Exit(run(exec.Command(exe, rest...)))
}

func indexOfExecutable(args []string) int {
	for i, a := range args {
		if strings.HasSuffix(strings.ToLower(a), ".exe") {
			return i
		}
	}
	return -1
}

func runNativeSevenZip(args []string) (int, bool) {
	if !extractsSevenZipArchive(args) {
		return 0, false
	}
	candidates := nativeCandidates()
	if len(candidates) == 0 {
		return 0, false
	}
	converted, ok := convertArguments(args)
	if !ok {
		return 0, false
	}
	workDir, err := os.MkdirTemp("", "navigraph-7z-")
	if err != nil {
		return 0, false
	}
	defer os.RemoveAll(workDir)
	unixWorkDir, ok := unixPath(workDir)
	if !ok {
		return 0, false
	}
	argv := append([]string{unixShell, "-c", nativeScript(candidates), unixShell, unixWorkDir}, converted...)
	files := []*os.File{os.Stdin, os.Stdout, os.Stderr}
	if _, err := os.StartProcess(windowsPathOf(unixShell), argv, &os.ProcAttr{Files: files}); err != nil {
		return 0, false
	}
	code, ok := waitForStatus(workDir)
	if !ok || code == shellCannotRun {
		return 0, false
	}
	if output, err := os.ReadFile(filepath.Join(workDir, "output")); err == nil {
		os.Stdout.Write(output)
	}
	return code, true
}

func extractsSevenZipArchive(args []string) bool {
	return len(args) > 1 && strings.EqualFold(filepath.Ext(args[1]), ".7z")
}

func nativeCandidates() []nativeSevenZip {
	if wineUnixFileName.Find() != nil || !fileExists(windowsPathOf(unixShell)) {
		return nil
	}
	return availableSevenZips(hostRoot, func(unix string) bool { return fileExists(windowsPathOf(unix)) })
}

func convertArguments(args []string) ([]string, bool) {
	converted := make([]string, 0, len(args))
	for _, a := range args {
		c, ok := convertArgument(a)
		if !ok {
			return nil, false
		}
		converted = append(converted, c)
	}
	return converted, true
}

func waitForStatus(workDir string) (int, bool) {
	status := filepath.Join(workDir, "status")
	output := filepath.Join(workDir, "output")
	started := time.Now()
	for {
		if data, err := os.ReadFile(status); err == nil {
			if code, err := strconv.Atoi(strings.TrimSpace(string(data))); err == nil {
				return code, true
			}
		}
		if time.Since(started) > startTimeout && !fileExists(output) {
			return 0, false
		}
		time.Sleep(statusPoll)
	}
}

func convertArgument(a string) (string, bool) {
	if len(a) > 2 && strings.EqualFold(a[:2], "-o") {
		u, ok := unixPath(a[2:])
		return a[:2] + u, ok
	}
	if isWindowsAbsolute(a) {
		return unixPath(a)
	}
	return a, true
}

func isWindowsAbsolute(p string) bool {
	return filepath.IsAbs(p) && filepath.VolumeName(p) != ""
}

func unixPath(p string) (string, bool) {
	p, err := filepath.Abs(p)
	if err != nil {
		return "", false
	}
	var tail []string
	for {
		if u, ok := wineUnixName(p); ok {
			return path.Join(append([]string{u}, tail...)...), true
		}
		parent := filepath.Dir(p)
		if parent == p {
			return "", false
		}
		tail = append([]string{filepath.Base(p)}, tail...)
		p = parent
	}
}

func wineUnixName(p string) (string, bool) {
	wide, err := syscall.UTF16PtrFromString(p)
	if err != nil {
		return "", false
	}
	r, _, _ := wineUnixFileName.Call(uintptr(unsafe.Pointer(wide)))
	if r == 0 {
		return "", false
	}
	name := cString(r)
	heap, _, _ := getProcessHeap.Call()
	heapFree.Call(heap, 0, r)
	return name, name != ""
}

func cString(ptr uintptr) string {
	base := *(*unsafe.Pointer)(unsafe.Pointer(&ptr))
	var b []byte
	for i := uintptr(0); ; i++ {
		c := *(*byte)(unsafe.Add(base, i))
		if c == 0 {
			return string(b)
		}
		b = append(b, c)
	}
}

func windowsPathOf(unix string) string {
	return `Z:` + strings.ReplaceAll(unix, "/", `\`)
}

func run(cmd *exec.Cmd) int {
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode()
		}
		return 1
	}
	return 0
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}
