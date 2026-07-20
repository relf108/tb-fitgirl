/// API key storage (TorBox and optional SteamGridDB).
///
/// Priority order for reads:
///   1. OS keyring via `secret-tool` (libsecret)
///   2. ~/.config/tb-fitgirl/<file>  (mode 0600, fallback when no keyring)
///   3. Environment variable (read-only override)
///
/// Writes go to the keyring when available, otherwise to the config file.
library;

import 'dart:io';

/// TorBox API key (required).
const torboxKeyStore = ApiKeyStore(
  configName: 'api_key',
  keyringAttrs: ['service', 'tb-fitgirl'],
  keyringLabel: 'TorBox API key (tb-fitgirl)',
  envVar: 'TORBOX_API_KEY',
);

/// SteamGridDB API key (optional; upgrades search thumbnails).
const steamGridDbKeyStore = ApiKeyStore(
  configName: 'steamgriddb_api_key',
  keyringAttrs: ['service', 'tb-fitgirl-steamgriddb'],
  keyringLabel: 'SteamGridDB API key (tb-fitgirl)',
  envVar: 'STEAMGRIDDB_API_KEY',
);

class ApiKeyStore {
  const ApiKeyStore({
    required this.configName,
    required this.keyringAttrs,
    required this.keyringLabel,
    required this.envVar,
  });

  final String configName;
  final List<String> keyringAttrs;
  final String keyringLabel;
  final String envVar;

  File get _configFile {
    final home = Platform.environment['HOME'] ?? '/tmp';
    return File('$home/.config/tb-fitgirl/$configName');
  }

  /// Load in priority order: keyring → config file → env var.
  Future<String?> load() async {
    // 1. Keyring.
    try {
      final result = await Process.run('secret-tool', ['lookup', ...keyringAttrs]);
      final key = (result.stdout as String).trim();
      if (result.exitCode == 0 && key.isNotEmpty) return key;
    } on ProcessException {
      // secret-tool not installed or no daemon — fall through.
    }

    // 2. Config file fallback.
    try {
      final key = (await _configFile.readAsString()).trim();
      if (key.isNotEmpty) return key;
    } on IOException {
      // File missing or unreadable — fall through.
    }

    // 3. Environment variable.
    final env = Platform.environment[envVar]?.trim();
    return (env != null && env.isNotEmpty) ? env : null;
  }

  /// Store the key. Returns true if persisted to keyring or config file.
  Future<bool> save(String key) async {
    // Try keyring first.
    try {
      final process = await Process.start('secret-tool', [
        'store',
        '--label=$keyringLabel',
        ...keyringAttrs,
      ]);
      process.stdin.write(key);
      await process.stdin.close();
      if (await process.exitCode == 0) return true;
    } on ProcessException {
      // No secret-tool or no daemon — fall through to config file.
    }

    // Fallback: write to config file with mode 0600.
    try {
      final file = _configFile;
      await file.parent.create(recursive: true);
      await file.writeAsString(key);
      await Process.run('chmod', ['600', file.path]);
      return true;
    } on IOException {
      return false;
    }
  }

  Future<void> clear() async {
    try {
      await Process.run('secret-tool', ['clear', ...keyringAttrs]);
    } on ProcessException {
      // Ignore.
    }
    try {
      await _configFile.delete();
    } on IOException {
      // File wasn't there.
    }
  }
}

