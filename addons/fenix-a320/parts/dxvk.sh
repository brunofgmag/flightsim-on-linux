#!/usr/bin/env bash

DXVK_DLLS=(d3d11.dll dxgi.dll)
DXVK_FILTER_PATTERN='^[[:space:]]*dxvk[.]deviceFilter[[:space:]]*='
DXVK_BUILD_COMMAND="bin/flightsim-on-linux dxvk build"
DXVK_GPU=""
DXVK_GPU_ERROR=""
DXVK_MODE=""
DXVK_MODE_ERROR=""

dxvk_cache() {
  echo "$FSOL_CACHE/dxvk-fenix"
}

dxvk_filter_line() {
  printf 'dxvk.deviceFilter = "%s"' "$1"
}

dxvk_conf_names() {
  local conf="$1" line="$2" filters
  [ -f "$conf" ] || return 1
  filters="$(tr -d '\r' < "$conf" | grep -E -- "$DXVK_FILTER_PATTERN" || true)"
  [ "$filters" = "$line" ]
}

dxvk_probe_gpu() {
  local tool="${FSOL_DISCRETE_GPU:-$FSOL_ROOT/tools/discrete-gpu.py}" errors status=0
  errors="$(mktemp)"
  DXVK_GPU="$("$tool" 2> "$errors")" || status=$?
  DXVK_GPU_ERROR="$(tr '\n' ' ' < "$errors" | sed 's/ *$//')"
  rm -f "$errors"
  [ "$status" -eq 0 ] || DXVK_GPU_ERROR="${DXVK_GPU_ERROR:-$(basename "$tool") exited with $status}"
  return "$status"
}

dxvk_probe_mode() {
  DXVK_MODE=""
  DXVK_MODE_ERROR=""
  if [ ! -f "$FENIX_CONFIG" ]; then
    DXVK_MODE_ERROR="fenixConfig.xml is missing; start the Fenix once and sign in"
    return 1
  fi
  DXVK_MODE="$(sed -n 's:.*<displayMode>\([^<]*\)</displayMode>.*:\1:p' "$FENIX_CONFIG" | head -n 1 | tr -d '[:space:]')"
  case "$DXVK_MODE" in
    CPU | GPU) return 0 ;;
    *) DXVK_MODE_ERROR="displayMode in fenixConfig.xml is '${DXVK_MODE:-missing}', expected CPU or GPU; set it in the Fenix settings"
      return 1 ;;
  esac
}

dxvk_cache_complete() {
  local dll
  for dll in "${DXVK_DLLS[@]}"; do
    [ -f "$(dxvk_cache)/$dll" ] || return 1
  done
}

dxvk_cache_message() {
  echo "$(dxvk_cache) has no ${DXVK_DLLS[*]}; run $DXVK_BUILD_COMMAND"
}

dxvk_folders() {
  echo ""
  [ ! -d "$FENIX_DEPS/displays" ] || echo displays
}

dxvk_name() {
  echo "${1:+$1/}$2"
}

dxvk_restore() {
  fenix_restore "$FENIX_DEPS/$1" "$1" deps
}

dxvk_write_conf() {
  local name="$1" conf="$FENIX_DEPS/$1" line kept
  line="$(dxvk_filter_line "$2")"
  if dxvk_conf_names "$conf" "$line"; then
    echo "already $name names the GPU"
    return 0
  fi
  fenix_stash "$conf" "$name"
  kept="$(grep -v -E -- "$DXVK_FILTER_PATTERN" "$conf" 2> /dev/null || true)"
  {
    [ -z "$kept" ] || printf '%s\n' "$kept"
    printf '%s\n' "$line"
  } > "$conf"
  echo "changed $name names the GPU: $2"
}

dxvk_copy_dll() {
  local name="$1" source
  source="$(dxvk_cache)/$(basename "$name")"
  if cmp -s "$source" "$FENIX_DEPS/$name"; then
    echo "already $name is the patched DXVK"
    return 0
  fi
  fenix_stash "$FENIX_DEPS/$name" "$name"
  cp "$source" "$FENIX_DEPS/$name"
  echo "changed $name is the patched DXVK"
}

dxvk_gpu_mode() {
  [ "$DXVK_MODE" = GPU ]
}

dxvk_apply_filter() {
  local folder name
  while IFS= read -r folder; do
    name="$(dxvk_name "$folder" dxvk.conf)"
    if [ -n "$DXVK_GPU" ]; then
      dxvk_write_conf "$name" "$DXVK_GPU"
    else
      dxvk_restore "$name"
    fi
  done < <(dxvk_folders)
}

dxvk_apply_dlls() {
  local folder dll
  while IFS= read -r folder; do
    for dll in "${DXVK_DLLS[@]}"; do
      if dxvk_gpu_mode; then
        dxvk_copy_dll "$(dxvk_name "$folder" "$dll")"
      else
        dxvk_restore "$(dxvk_name "$folder" "$dll")"
      fi
    done
  done < <(dxvk_folders)
}

dxvk_apply() {
  [ -d "$FENIX_DEPS" ] || fail "$FENIX_DEPS is missing; install the Fenix first" || return 1
  dxvk_probe_gpu || fail "device filter: $DXVK_GPU_ERROR" || return 1
  dxvk_probe_mode || fail "$DXVK_MODE_ERROR" || return 1
  if dxvk_gpu_mode; then
    dxvk_cache_complete || fail "$(dxvk_cache_message)" || return 1
  fi
  dxvk_apply_filter
  dxvk_apply_dlls
}

dxvk_check_filter() {
  local folder name status=0
  if ! dxvk_probe_gpu; then
    fail "device filter: $DXVK_GPU_ERROR"
    return
  fi
  if [ -z "$DXVK_GPU" ]; then
    ok "one GPU, no device filter needed"
    return
  fi
  while IFS= read -r folder; do
    name="$(dxvk_name "$folder" dxvk.conf)"
    if dxvk_conf_names "$FENIX_DEPS/$name" "$(dxvk_filter_line "$DXVK_GPU")"; then
      ok "$name names the GPU: $DXVK_GPU"
    else
      fail "$name in deps does not contain $(dxvk_filter_line "$DXVK_GPU") as its only dxvk.deviceFilter line; run apply" || status=1
    fi
  done < <(dxvk_folders)
  return "$status"
}

dxvk_check_dlls() {
  local folder dll name status=0
  if ! dxvk_cache_complete; then
    fail "$(dxvk_cache_message)"
    return
  fi
  while IFS= read -r folder; do
    for dll in "${DXVK_DLLS[@]}"; do
      name="$(dxvk_name "$folder" "$dll")"
      if cmp -s "$(dxvk_cache)/$dll" "$FENIX_DEPS/$name"; then
        ok "$name in deps is the patched DXVK"
      else
        fail "$name in deps is not the patched DXVK of $(dxvk_cache); run apply" || status=1
      fi
    done
  done < <(dxvk_folders)
  return "$status"
}

dxvk_check_mode() {
  if ! dxvk_probe_mode; then
    fail "$DXVK_MODE_ERROR"
  elif dxvk_gpu_mode; then
    dxvk_check_dlls
  else
    ok "displays render on the CPU, no DXVK needed"
  fi
}

dxvk_check() {
  local status=0
  dxvk_check_filter || status=1
  dxvk_check_mode || status=1
  return "$status"
}

dxvk_undo() {
  local folder name
  while IFS= read -r folder; do
    for name in dxvk.conf "${DXVK_DLLS[@]}"; do
      dxvk_restore "$(dxvk_name "$folder" "$name")"
    done
  done < <(dxvk_folders)
}
