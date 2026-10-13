# Retain on Flathub

What is here, and the steps to submit it. Nothing has been sent to Flathub; every step below
that touches GitHub is yours to take.

## The files

| File | What it is |
| --- | --- |
| `io.github.jackicus.Retain.json` | Flathub's manifest: the release build of a git tag |
| `io.github.jackicus.Retain.Devel.json` | The development build of this checkout ("Retain (Development)"); GNOME Builder and CI use it |
| `libspiel.json` | libspiel and libspeechprovider (Read Aloud through Spiel's voices); not in the runtime |
| `python3-speechd.json` | Speech Dispatcher's Python client (Read Aloud's fallback); the daemon is the host's |
| `lint-exceptions.json` | Waives `appid-url-not-reachable` until step 1 below; then delete it |

Runtime: `org.gnome.Platform//51` (GTK 4.24, libadwaita 1.10, WebKitGTK 6.0, GtkSourceView 5,
Python 3.14 with `compression.zstd`, blueprint-compiler 0.22.2 in the SDK).

Permissions, and why:

- `--share=network`: only so a browser on the host reaches the AnkiConnect API that Retain
  serves on 127.0.0.1:8765 when the user turns it on in Preferences (Yomitan and other
  card-mining extensions). Retain makes no connections of its own.
- `--socket=pulseaudio`: cards' sounds (GStreamer).
- `--filesystem=xdg-run/speech-dispatcher:ro`, `--talk-name=org.espeak.Speech.Provider`,
  `--talk-name=ai.piper.Speech.Provider`: Read Aloud, through the host's Speech Dispatcher or
  Spiel's voice providers.
- No filesystem access otherwise: imports, exports and the sync folder come through the file
  chooser portal, which keeps the grant.

## Building and checking it locally

```sh
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51 org.gnome.Platform//51

# The development build of this checkout, installed as io.github.jackicus.Retain.Devel
flatpak run org.flatpak.Builder --user --install --force-clean \
  --state-dir=build/flatpak/state --repo=build/flatpak/repo \
  build/flatpak/build-devel build-aux/flatpak/io.github.jackicus.Retain.Devel.json

# The linters Flathub runs (the repo check also wants screenshots mirrored, which only
# Flathub's own build does: appstream-external-screenshot-url and
# appstream-screenshots-not-mirrored-in-ostree are expected locally)
lint() { flatpak run --command=flatpak-builder-lint org.flatpak.Builder "$@"; }
lint --exceptions --user-exceptions build-aux/flatpak/lint-exceptions.json \
  manifest build-aux/flatpak/io.github.jackicus.Retain.json
lint appstream build/flatpak/build-devel/files/share/metainfo/io.github.jackicus.Retain.Devel.metainfo.xml
lint --exceptions --user-exceptions build-aux/flatpak/lint-exceptions.json repo build/flatpak/repo
```

When `flatpak run` complains of `/run/user/…/doc/by-app/…`, the document portal is not
running: add `--no-documents-portal` after `flatpak run`. To try it on a throwaway collection,
add `--filesystem="$PWD/build/flatpak" --env=RETAIN_DATA_DIR="$PWD/build/flatpak/data"`.

## Submitting

1. **Make the app ID's URL real.** Flathub requires `https://github.com/jackicus/retain` to
   exist for `io.github.jackicus.Retain` (the linter's `appid-url-not-reachable`). Rename the
   repository from GNOME-Retain to **Retain** (GitHub, Settings, General, Repository name;
   GitHub redirects the old URLs and clones). Then, in one commit: the URLs in
   `data/io.github.jackicus.Retain.metainfo.xml.in` (homepage, bugtracker, help, vcs-browser,
   contribute, and the screenshots'), README.md, `build-aux/aur/PKGBUILD`, the `url` of the
   git source in `io.github.jackicus.Retain.json`, and `git remote set-url origin
   https://github.com/Jackicus/Retain.git`; delete `lint-exceptions.json` and the
   `--exceptions --user-exceptions …` arguments in `.github/workflows/ci.yml`.
2. **Tag a release.** The metainfo has a `<release>` with notes for the version; `version`
   in meson.build matches it; the screenshot URLs name the tag. Commit, tag (`v0.2.0`), push
   the tag. In `io.github.jackicus.Retain.json` set `tag` to the tag and add
   `"commit": "<git rev-parse v0.2.0^{commit}>"` beside it (the linter warns
   `module-retain-source-git-no-commit-with-tag` until then). Build it and run the linters
   (above): all clean.
3. **Fork and branch.** Fork https://github.com/flathub/flathub on GitHub (all branches, not
   only master), then:

   ```sh
   git clone --branch=new-pr git@github.com:Jackicus/flathub.git
   cd flathub
   git checkout -b add-retain new-pr
   cp ../Retain/build-aux/flatpak/{io.github.jackicus.Retain,libspiel,python3-speechd}.json .
   git add . && git commit -m "Add io.github.jackicus.Retain"
   git push -u origin add-retain
   ```

   Only the release manifest and the two module files go; not the .Devel manifest, not
   `lint-exceptions.json`.
4. **Open the pull request** against `flathub/flathub`'s **new-pr** branch (not master),
   titled "Add io.github.jackicus.Retain", and fill in its checklist. Comment `bot, build`
   to have Flathub's builder try it; fix what it or a reviewer asks for on the same branch.
5. **Expect questions on** `--share=network` (the AnkiConnect API above; it listens on
   127.0.0.1 only, and is off until turned on) and the speech permissions.
6. **After it is merged**, Flathub makes `github.com/flathub/io.github.jackicus.Retain` and
   invites you to it. Updates are pull requests there (a new tag and commit in the manifest).
   Verify the app on flathub.org (Developer portal, sign in with GitHub as Jackicus).

## Not yet tried in the Flatpak

- Syncing through a folder granted by the portal (a `/run/user/…/doc/…` path), and with
  Syncthing or Nextcloud on two real machines.
- Read Aloud with Spiel's providers installed on the host.
