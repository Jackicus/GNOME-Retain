# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

project(
  'retain',
  version: '0.1.0',
  meson_version: '>= 1.2.0',  # get_option('python.bytecompile') (src/meson.build)
  license: 'GPL-2.0-or-later',
  default_options: ['warning_level=2', 'werror=false'],
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

base_id = 'io.github.jackicus.Retain'
resource_path = '/io/github/jackicus/Retain'

if get_option('profile') == 'development'
  application_id = base_id + '.Devel'
  git_rev = run_command('git', 'rev-parse', '--short', 'HEAD', check: false).stdout().strip()
  version = meson.project_version() + (git_rev == '' ? '-devel' : '-' + git_rev)
else
  application_id = base_id
  version = meson.project_version()
endif

dependency('glib-2.0', version: '>= 2.84')
dependency('gtk4', version: '>= 4.20')
dependency('libadwaita-1', version: '>= 1.9')
dependency('pygobject-3.0', version: '>= 3.50')
# The card face is rendered by WebKitGTK (src/widgets/card_view.py); without it at run time
# the app falls back to a plain rendering, so the dependency is a build-time check only.
dependency('webkitgtk-6.0', required: false)

pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'retain'

subdir('data')
subdir('src')
subdir('po')

# The unit tests, for `meson test` and IDEs. scripts/check.sh runs them itself (after meson
# compile, for the widget tests' gresource) and the data tests with --no-suite unit.
test('unit', python3,
  args: ['-m', 'unittest', 'discover', '-s', 'tests'],
  workdir: meson.project_source_root(),
  timeout: 240,
  suite: 'unit',
)

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
