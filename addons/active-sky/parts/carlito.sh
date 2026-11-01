#!/usr/bin/env bash
carlito_installed() {
  command -v fc-list > /dev/null && [ -n "$(fc-list Carlito : family 2> /dev/null)" ]
}
