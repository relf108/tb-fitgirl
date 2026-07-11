/// Client for the Python JSON-lines stdio bridge (`tb-fitgirl-bridge` when
/// installed, or `python -m tb_fitgirl.bridge` in a dev checkout).
///
/// One bridge process is spawned per operation; cancellation kills the whole
/// process group (Proton/installer children included). All TorBox/Proton
/// logic lives in Python — this file only speaks the line protocol.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

class BridgeProgress {
  const BridgeProgress({
    required this.phase,
    required this.done,
    required this.total,
    required this.rate,
    required this.message,
  });

  final String phase;
  final int done;
  final int total;
  final double rate;
  final String message;

  factory BridgeProgress.fromJson(Map<String, dynamic> json) => BridgeProgress(
        phase: json['phase'] as String? ?? '',
        done: (json['done'] as num? ?? 0).toInt(),
        total: (json['total'] as num? ?? 0).toInt(),
        rate: (json['rate'] as num? ?? 0).toDouble(),
        message: json['message'] as String? ?? '',
      );

  double? get fraction => total > 0 ? (done / total).clamp(0.0, 1.0) : null;
}

class BridgeException implements Exception {
  const BridgeException(this.message, {this.code});

  final String message;
  final String? code;

  @override
  String toString() => message;
}

/// How to launch the bridge process.
sealed class _BridgeLaunch {
  const _BridgeLaunch();
}

/// Installed Nix/system package: `tb-fitgirl-bridge` is on PATH.
class _InstalledBridge extends _BridgeLaunch {
  const _InstalledBridge();
}

/// Dev checkout: `python3 -m tb_fitgirl.bridge` inside the repo directory.
class _DevBridge extends _BridgeLaunch {
  const _DevBridge(this.backendDir);
  final Directory backendDir;
}

/// Resolve how to launch the bridge:
/// 1. `tb-fitgirl-bridge` on PATH (Nix / system install).
/// 2. Dev checkout discovered via TBFG_BACKEND or proximity to the executable.
_BridgeLaunch? _resolveBridge() {
  // Prefer the installed wrapper when it exists on PATH.
  final pathDirs =
      (Platform.environment['PATH'] ?? '').split(':').map(Directory.new);
  for (final dir in pathDirs) {
    if (File('${dir.path}/tb-fitgirl-bridge').existsSync()) {
      return const _InstalledBridge();
    }
  }

  // Fall back to a dev checkout (useful for `flutter run`).
  final envDir = Platform.environment['TBFG_BACKEND'];
  final candidates = <Directory>[
    if (envDir != null) Directory(envDir),
    Directory.current,
    Directory.current.parent,
    File(Platform.resolvedExecutable).parent,
    File(Platform.resolvedExecutable).parent.parent,
  ];
  for (final dir in candidates) {
    if (File('${dir.path}/src/tb_fitgirl/bridge.py').existsSync()) {
      return _DevBridge(dir);
    }
  }

  return null;
}

/// A single request/response exchange with a dedicated bridge process.
class BridgeOperation {
  BridgeOperation._(this._process, this._result);

  final Process _process;
  final Future<Map<String, dynamic>> _result;
  bool _cancelled = false;

  /// Resolves with the `result` event data, or throws [BridgeException].
  Future<Map<String, dynamic>> get result => _result;

  static Future<BridgeOperation> start(
    String op,
    Map<String, dynamic> args, {
    void Function(BridgeProgress progress)? onProgress,
  }) async {
    final launch = _resolveBridge();
    if (launch == null) {
      throw const BridgeException(
        'Back end not found. Install tb-fitgirl or set TBFG_BACKEND to the checkout.',
      );
    }

    // The bridge makes itself a process-group leader (os.setpgid) so
    // cancel() can kill the whole tree (Proton unpackers included). Do NOT
    // wrap it in setsid: losing the session/terminal makes Proton's
    // unpacker hang (kernel snd_power_wait on the installer's audio).
    final Process process;
    if (launch is _InstalledBridge) {
      process = await Process.start(
        'tb-fitgirl-bridge',
        [],
        environment: Platform.environment,
      );
    } else {
      final dev = launch as _DevBridge;
      final python = Platform.environment['TBFG_PYTHON'] ?? 'python3';
      process = await Process.start(
        python,
        ['-m', 'tb_fitgirl.bridge'],
        workingDirectory: dev.backendDir.path,
        environment: {
          ...Platform.environment,
          'PYTHONPATH': '${dev.backendDir.path}/src',
        },
      );
    }

    final completer = Completer<Map<String, dynamic>>();
    final stderrBuf = StringBuffer();
    process.stderr.transform(utf8.decoder).listen(stderrBuf.write);
    process.stdout
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen(
      (line) {
        if (line.trim().isEmpty || completer.isCompleted) return;
        final Object? decoded;
        try {
          decoded = jsonDecode(line);
        } on FormatException {
          return; // ignore stray non-JSON output
        }
        if (decoded is! Map<String, dynamic>) return;
        final data = decoded['data'];
        final payload =
            data is Map<String, dynamic> ? data : <String, dynamic>{};
        switch (decoded['event']) {
          case 'progress':
            onProgress?.call(BridgeProgress.fromJson(payload));
          case 'result':
            completer.complete(payload);
          case 'error':
            completer.completeError(
              BridgeException(
                payload['message'] as String? ?? 'Unknown bridge error',
                code: payload['code'] as String?,
              ),
            );
        }
      },
      onDone: () {
        if (!completer.isCompleted) {
          completer.completeError(
            BridgeException(
              'Bridge exited before replying. ${stderrBuf.toString().trim()}'
                  .trim(),
              code: 'BRIDGE_EXIT',
            ),
          );
        }
      },
    );

    process.stdin.writeln(jsonEncode({'id': 1, 'op': op, 'args': args}));
    await process.stdin.flush();
    await process.stdin.close();

    final operation = BridgeOperation._(process, completer.future);
    // Reap the bridge once the op settles. Errors surface to callers via
    // [result]; this side listener must swallow them (.ignore()), and a
    // bare catchError((_) {}) would throw: its handler has to return the
    // future's value type.
    completer.future.whenComplete(() {
      if (!operation._cancelled) process.kill();
    }).ignore();
    return operation;
  }

  /// Kill the bridge and its whole process group (installer children too).
  Future<void> cancel() async {
    _cancelled = true;
    await Process.run('kill', ['-TERM', '--', '-${_process.pid}']);
    _process.kill();
  }
}

/// Convenience wrapper: run an op to completion.
Future<Map<String, dynamic>> runBridgeOp(
  String op,
  Map<String, dynamic> args, {
  void Function(BridgeProgress progress)? onProgress,
}) async {
  final operation = await BridgeOperation.start(
    op,
    args,
    onProgress: onProgress,
  );
  return operation.result;
}
