"""One module per language, each a flat ``TRANSLATIONS`` dict.

Flat and prefixed rather than nested: `ht.cut_end` reads as well as a tree
would and a missing key is a one-line diff between two files, which is what
`test_i18n.py` compares. The prefix says who owns the word — `ui.` for the
layer itself, `signin.` / `legal.` / `quota.` / `notifications.` / `status.`
for the shared components, `ht.` for HomeTube.

English is the reference: a key exists here first, and a language that has not
caught up falls back to it rather than showing a hole (`content_sdk.i18n.t`).
"""
