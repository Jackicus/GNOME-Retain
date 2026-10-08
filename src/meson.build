# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

# An older blueprint-compiler on PATH (as some distributions ship) falls back to the wrap's.
blueprint_compiler = find_program('blueprint-compiler', version: '>= 0.22', required: false)
if not blueprint_compiler.found()
  # Fall back to the wrap. Its configured wrapper can't run uninstalled
  # (it can't find its own module), so run the source tree's script instead.
  subproject('blueprint-compiler')
  blueprint_compiler = find_program(
    meson.project_source_root() / 'subprojects' / 'blueprint-compiler' / 'blueprint-compiler.py',
  )
endif

# One target per .blp, each naming its .ui, so an edited .blp is recompiled. The .ui files
# land flat in the build directory, which the gresource lists them from.
blueprints = []
foreach blueprint : files(
    'pages/today.blp',
    'pages/deck.blp',
    'pages/browse.blp',
    'pages/stats.blp',
    'pages/review.blp',
    'dialogs/add_edit.blp',
    'dialogs/deck_options.blp',
    'dialogs/custom_study.blp',
    'dialogs/import_export.blp',
    'dialogs/preferences.blp',
    'window.blp',
  )
  blueprints += custom_target(
    input: blueprint,
    output: '@BASENAME@.ui',
    command: [blueprint_compiler, 'compile', '--output', '@OUTPUT@', '@INPUT@'],
  )
endforeach

python3 = python.find_installation('python3')

# The metainfo data/ generates, under a name of its own in the gresource: the About dialog is
# built from it (its release notes included).
metainfo_resource = custom_target(
  input: metainfo_file,
  output: 'metainfo.xml',
  command: [python3, '-c', 'import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])',
            '@INPUT@', '@OUTPUT@'],
)

gnome.compile_resources(
  'retain',
  'retain.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
  dependencies: blueprints + [metainfo_resource],
)

configure_file(
  input: 'retain.in',
  output: 'retain',
  configuration: {
    'PYTHON': python3.full_path(),
    'VERSION': version,
    'APP_ID': application_id,
    'BASE_ID': base_id,
    'PROFILE': get_option('profile'),
    'localedir': get_option('prefix') / get_option('localedir'),
    'pkgdatadir': pkgdatadir,
  },
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr-xr-x',
)

install_data(
  '__init__.py',
  'ankiconnect.py',
  'answers.py',
  'apkg.py',
  'collection.py',
  'days.py',
  'deck_config.py',
  'exporting.py',
  'fsrs.py',
  'i18n.py',
  'importing.py',
  'main.py',
  'media.py',
  'notetypes.py',
  'optimizer.py',
  'scheduler.py',
  'schema.py',
  'search.py',
  'shortcuts.py',
  'sidebar.py',
  'speech.py',
  'stats.py',
  'sync.py',
  'template.py',
  'window.py',
  'workload.py',
  install_dir: moduledir,
)

install_data(
  'pages/__init__.py',
  'pages/browse.py',
  'pages/deck.py',
  'pages/review.py',
  'pages/stats.py',
  'pages/today.py',
  install_dir: moduledir / 'pages',
)

install_data(
  'dialogs/__init__.py',
  'dialogs/about.py',
  'dialogs/card_info.py',
  'dialogs/add_edit.py',
  'dialogs/custom_study.py',
  'dialogs/deck_name.py',
  'dialogs/deck_options.py',
  'dialogs/import_export.py',
  'dialogs/preferences.py',
  'dialogs/shortcuts.py',
  install_dir: moduledir / 'dialogs',
)

install_data(
  'widgets/__init__.py',
  'widgets/audio.py',
  'widgets/speech.py',
  'widgets/card_view.py',
  'widgets/charts.py',
  'widgets/counts.py',
  'widgets/field_editor.py',
  'widgets/html_text.py',
  'widgets/occlusion_editor.py',
  'widgets/util.py',
  install_dir: moduledir / 'widgets',
)

# Byte-compile the modules once they are installed (after every install_data above): they live
# outside site-packages, so Meson's python.bytecompile never reaches them, and a system install
# without bytecode imports twice as slowly.
bytecompile = get_option('python.bytecompile')
if bytecompile >= 0
  meson.add_install_script(
    python3,
    meson.project_source_root() / 'build-aux' / 'meson' / 'compile-python.py',
    get_option('datadir') / meson.project_name() / 'retain',
    bytecompile.to_string(),
    install_tag: 'runtime',
  )
endif
