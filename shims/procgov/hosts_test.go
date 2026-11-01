package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func existingOnly(files ...string) func(string) bool {
	set := map[string]bool{}
	for _, f := range files {
		set[f] = true
	}
	return func(p string) bool { return set[p] }
}

func TestArchLayoutUsesTheArchLoaderAndLibraries(t *testing.T) {
	exists := existingOnly("/run/host/usr/lib/7zip/7z", "/run/host/usr/lib/ld-linux-x86-64.so.2")
	got := availableSevenZips(hostRoot, exists)
	want := nativeSevenZip{"/run/host/usr/lib/7zip/7z", "/run/host/usr/lib/ld-linux-x86-64.so.2", "/run/host/usr/lib"}
	if len(got) != 1 || got[0] != want {
		t.Fatalf("got %v, want [%v]", got, want)
	}
}

func TestDebianLayoutUsesTheMultiarchLoader(t *testing.T) {
	exists := existingOnly("/run/host/usr/lib/p7zip/7z", "/run/host/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
	got := availableSevenZips(hostRoot, exists)
	want := nativeSevenZip{"/run/host/usr/lib/p7zip/7z", "/run/host/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", "/run/host/usr/lib/x86_64-linux-gnu:/run/host/usr/lib"}
	if len(got) != 1 || got[0] != want {
		t.Fatalf("got %v, want [%v]", got, want)
	}
}

func TestFedoraLayoutUsesTheLib64Loader(t *testing.T) {
	exists := existingOnly("/run/host/usr/libexec/p7zip/7z", "/run/host/usr/lib64/ld-linux-x86-64.so.2")
	got := availableSevenZips(hostRoot, exists)
	want := nativeSevenZip{"/run/host/usr/libexec/p7zip/7z", "/run/host/usr/lib64/ld-linux-x86-64.so.2", "/run/host/usr/lib64"}
	if len(got) != 1 || got[0] != want {
		t.Fatalf("got %v, want [%v]", got, want)
	}
}

func TestBinFallbacksAreOffered(t *testing.T) {
	exists := existingOnly("/run/host/usr/bin/7zz", "/run/host/usr/lib64/ld-linux-x86-64.so.2")
	got := availableSevenZips(hostRoot, exists)
	if len(got) != 1 || got[0].binary != "/run/host/usr/bin/7zz" {
		t.Fatalf("got %v", got)
	}
}

func TestNothingAvailableMeansNoCandidates(t *testing.T) {
	if got := availableSevenZips(hostRoot, existingOnly()); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
	if got := availableSevenZips(hostRoot, existingOnly("/run/host/usr/lib/7zip/7z")); len(got) != 0 {
		t.Fatalf("a binary without a loader must not be offered, got %v", got)
	}
}

func TestTheCandidatesComeInTheOrderOfTheList(t *testing.T) {
	exists := existingOnly("/run/host/usr/lib/7zip/7z", "/run/host/usr/bin/7zz", "/run/host/usr/lib/ld-linux-x86-64.so.2")
	got := availableSevenZips(hostRoot, exists)
	if len(got) != 2 || got[0].binary != "/run/host/usr/lib/7zip/7z" || got[1].binary != "/run/host/usr/bin/7zz" {
		t.Fatalf("got %v", got)
	}
}

type scriptRun struct {
	status string
	output string
}

func writeExecutable(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o755); err != nil {
		t.Fatal(err)
	}
}

func runScript(t *testing.T, candidates []nativeSevenZip, args ...string) scriptRun {
	t.Helper()
	dir := t.TempDir()
	argv := append([]string{"-c", nativeScript(candidates), "sh", dir}, args...)
	if err := exec.Command("/bin/sh", argv...).Run(); err != nil {
		t.Fatal(err)
	}
	status, err := os.ReadFile(filepath.Join(dir, "status"))
	if err != nil {
		t.Fatal(err)
	}
	output, _ := os.ReadFile(filepath.Join(dir, "output"))
	return scriptRun{strings.TrimSpace(string(status)), string(output)}
}

func fakeLoader(t *testing.T, path string) {
	writeExecutable(t, path, "#!/bin/sh\nshift 2\nexec \"$@\"\n")
}

func TestTheScriptReportsTheExitCodeOfTheFirstCandidateThatRuns(t *testing.T) {
	root := t.TempDir()
	loader := filepath.Join(root, "loader")
	fakeLoader(t, loader)
	failing := filepath.Join(root, "7z-fails")
	writeExecutable(t, failing, "#!/bin/sh\necho \"ERROR: broken $2\"\nexit 2\n")
	got := runScript(t, []nativeSevenZip{{failing, loader, "/lib"}}, "x", "archive.7z")
	if got.status != "2" || !strings.Contains(got.output, "ERROR: broken archive.7z") {
		t.Fatalf("got %+v", got)
	}
}

func TestTheScriptMovesOnWhenACandidateCannotRun(t *testing.T) {
	root := t.TempDir()
	loader := filepath.Join(root, "loader")
	fakeLoader(t, loader)
	missing := filepath.Join(root, "not-an-executable")
	working := filepath.Join(root, "7z-works")
	writeExecutable(t, working, "#!/bin/sh\necho \"extracted $2\"\nexit 0\n")
	got := runScript(t, []nativeSevenZip{{missing, loader, "/lib"}, {working, loader, "/lib"}}, "x", "archive.7z")
	if got.status != "0" || !strings.Contains(got.output, "extracted archive.7z") {
		t.Fatalf("got %+v", got)
	}
}

func TestTheScriptAnswers127WhenNoCandidateRuns(t *testing.T) {
	root := t.TempDir()
	loader := filepath.Join(root, "loader")
	fakeLoader(t, loader)
	got := runScript(t, []nativeSevenZip{{filepath.Join(root, "missing"), loader, "/lib"}}, "x", "archive.7z")
	if got.status != "127" {
		t.Fatalf("got %+v", got)
	}
}

func TestTheScriptPassesTheLibraryPathToTheLoader(t *testing.T) {
	root := t.TempDir()
	loader := filepath.Join(root, "loader")
	writeExecutable(t, loader, "#!/bin/sh\necho \"$1 $2\"\nexit 0\n")
	got := runScript(t, []nativeSevenZip{{"/seven", loader, "/a:/b"}}, "x")
	if got.output != "--library-path /a:/b\n" {
		t.Fatalf("got %+v", got)
	}
}
