package main

import "strings"

const (
	hostRoot       = "/run/host"
	shellCannotRun = 127
)

type nativeSevenZip struct {
	binary string
	loader string
	libs   string
}

type hostLoader struct {
	loader string
	libs   []string
}

var hostLoaders = []hostLoader{
	{"/usr/lib/ld-linux-x86-64.so.2", []string{"/usr/lib"}},
	{"/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", []string{"/usr/lib/x86_64-linux-gnu", "/usr/lib"}},
	{"/usr/lib64/ld-linux-x86-64.so.2", []string{"/usr/lib64"}},
}

var hostBinaries = []string{
	"/usr/lib/7zip/7z",
	"/usr/lib/p7zip/7z",
	"/usr/libexec/p7zip/7z",
	"/usr/bin/7z",
	"/usr/bin/7zz",
}

func libraryPath(root string, libs []string) string {
	rooted := make([]string, len(libs))
	for i, lib := range libs {
		rooted[i] = root + lib
	}
	return strings.Join(rooted, ":")
}

func availableSevenZips(root string, exists func(string) bool) []nativeSevenZip {
	var found []nativeSevenZip
	for _, binary := range hostBinaries {
		if !exists(root + binary) {
			continue
		}
		for _, loader := range hostLoaders {
			if exists(root + loader.loader) {
				found = append(found, nativeSevenZip{root + binary, root + loader.loader, libraryPath(root, loader.libs)})
			}
		}
	}
	return found
}

func nativeScript(candidates []nativeSevenZip) string {
	var script strings.Builder
	script.WriteString(`dir=$1; shift; `)
	for _, c := range candidates {
		script.WriteString(`'` + c.loader + `' --library-path '` + c.libs + `' '` + c.binary + `' "$@" > "$dir/output" 2>&1; code=$?; `)
		script.WriteString(`if [ "$code" -ne 127 ]; then echo "$code" > "$dir/status"; exit 0; fi; `)
	}
	script.WriteString(`echo 127 > "$dir/status"`)
	return script.String()
}
