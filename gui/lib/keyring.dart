/// TorBox API key storage in the OS keyring via `secret-tool` (libsecret).
///
/// No key ever touches a dotfile in the repo. If secret-tool is unavailable
/// the TORBOX_API_KEY environment variable still works as a read-only
/// fallback.
library;

import 'dart:io';

const _attrs = ['service', 'tb-fitgirl'];

class ApiKeyStore {
  const ApiKeyStore();

  /// Keyring first, then the TORBOX_API_KEY environment variable.
  Future<String?> load() async {
    try {
      final result = await Process.run('secret-tool', ['lookup', ..._attrs]);
      final key = (result.stdout as String).trim();
      if (result.exitCode == 0 && key.isNotEmpty) return key;
    } on ProcessException {
      // secret-tool not installed; fall through to the env var.
    }
    final env = Platform.environment['TORBOX_API_KEY']?.trim();
    return (env != null && env.isNotEmpty) ? env : null;
  }

  /// Store the key in the keyring. Returns false if secret-tool is missing
  /// or the keyring rejected the write.
  Future<bool> save(String key) async {
    try {
      final process = await Process.start('secret-tool', [
        'store',
        '--label=TorBox API key (tb-fitgirl)',
        ..._attrs,
      ]);
      process.stdin.write(key);
      await process.stdin.close();
      return await process.exitCode == 0;
    } on ProcessException {
      return false;
    }
  }

  Future<void> clear() async {
    try {
      await Process.run('secret-tool', ['clear', ..._attrs]);
    } on ProcessException {
      // Nothing stored anywhere we can clear.
    }
  }
}
