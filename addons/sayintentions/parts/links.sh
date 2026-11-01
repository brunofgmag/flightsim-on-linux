#!/usr/bin/env bash
SI_LINK_ATTRIBUTE="user.WINEREPARSE"
SI_STASH="${SI_BACKUP:?SI_BACKUP is not set}/links"

si_need_attr_tools() {
  local tool
  for tool in getfattr setfattr iconv base64; do
    command -v "$tool" > /dev/null || { echo "$tool is missing: install it (getfattr and setfattr are in the attr package); without it SayIntentions' packages in Community cannot be kept safe from the Update Hub" >&2; return 1; }
  done
}

si_link_value() {
  getfattr --only-values -n "$SI_LINK_ATTRIBUTE" "$1" 2> /dev/null
}

si_is_link() {
  si_link_value "$1" | iconv -f UTF-16LE -t UTF-8 -c 2> /dev/null | grep 'SayIntentions\.AI' > /dev/null
}

si_each_link() {
  local entry
  for entry in "${FSOL_COMMUNITY:?FSOL_COMMUNITY is not set}"/*; do
    [ -d "$entry" ] || continue
    si_is_link "$entry" && echo "$entry"
  done
  return 0
}

si_remove_links() {
  local entry
  while IFS= read -r entry; do
    if rmdir "$entry" 2> /dev/null; then
      echo "removed the Wine link $(basename "$entry") from Community; the Update Hub makes it again"
    else
      echo "could not remove $entry; it is not an empty link folder" >&2
    fi
  done < <(si_each_link)
}

si_stash_links() {
  local entry name
  mkdir -p "$SI_STASH"
  while IFS= read -r entry; do
    name="$(basename "$entry")"
    si_link_value "$entry" | base64 -w0 > "$SI_STASH/$name"
    if rmdir "$entry" 2> /dev/null; then
      echo "[sayintentions-links] kept and removed $name at $(date +%T)"
    else
      rm -f "$SI_STASH/$name"
    fi
  done < <(si_each_link)
}

si_restore_links() {
  local saved name
  [ -d "$SI_STASH" ] || return 0
  for saved in "$SI_STASH"/*; do
    [ -f "$saved" ] || continue
    name="$(basename "$saved")"
    if [ ! -e "$FSOL_COMMUNITY/$name" ]; then
      if mkdir "$FSOL_COMMUNITY/$name" && setfattr -n "$SI_LINK_ATTRIBUTE" -v "0s$(cat "$saved")" "$FSOL_COMMUNITY/$name"; then
        echo "[sayintentions-links] gave back $name at $(date +%T)"
      else
        echo "[sayintentions-links] could not give back $name; its value stays in $saved" >&2
        continue
      fi
    fi
    rm -f "$saved"
  done
  rmdir "$SI_STASH" 2> /dev/null || true
  return 0
}
