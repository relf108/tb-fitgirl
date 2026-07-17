import 'package:flutter/material.dart';

import '../bridge.dart';
import '../models.dart';

/// Drives the one-click install chain (cache -> download -> verify -> Proton
/// unpack -> shortcuts) and streams its progress.
class InstallScreen extends StatefulWidget {
  const InstallScreen({
    super.key,
    required this.title,
    required this.source,
    required this.apiKey,
  });

  final String title;
  final String source;
  final String apiKey;

  @override
  State<InstallScreen> createState() => _InstallScreenState();
}

class _InstallScreenState extends State<InstallScreen> {
  static const _phaseLabels = {
    'scrape': 'Finding repack',
    'cache': 'Waiting for TorBox',
    'download': 'Downloading',
    'verify': 'Verifying archives',
    'unpack': 'Installing (Proton unpack)',
    'shortcut': 'Adding shortcuts',
  };

  BridgeOperation? _operation;
  BridgeProgress? _progress;
  Map<String, dynamic>? _result;
  String? _error;
  bool _cancelled = false;
  final List<String> _log = [];

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void dispose() {
    // Leaving the screen mid-install cancels it: the bridge process group
    // (including any Proton unpacker) is killed.
    if (_result == null && _error == null && !_cancelled) {
      _operation?.cancel();
    }
    super.dispose();
  }

  Future<void> _start() async {
    try {
      final operation = await BridgeOperation.start(
          'install',
          {
            'target': widget.title,
            'source': widget.source,
            'api_key': widget.apiKey,
          },
          onProgress: _onProgress,
          onConfirm: _onConfirm,
          onSelectExe: _onSelectExe);
      _operation = operation;
      final result = await operation.result;
      if (!mounted) return;
      setState(() => _result = result);
    } on BridgeException catch (err) {
      if (!mounted) return;
      setState(() => _error = _cancelled ? 'Install cancelled.' : err.message);
    }
  }

  void _onProgress(BridgeProgress progress) {
    if (!mounted) return;
    setState(() {
      _progress = progress;
      if (progress.message.isNotEmpty &&
          (_log.isEmpty || _log.last != progress.message)) {
        _log.add(progress.message);
        if (_log.length > 200) _log.removeAt(0);
      }
    });
  }

  /// The bridge thinks the install is finished (game exe present, install
  /// directory quiet) but the installer hasn't exited. Ask the user whether
  /// to terminate it now or keep waiting.
  Future<bool> _onConfirm(String kind, String message) async {
    if (!mounted) return true;
    final answer = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Install looks done'),
        content: Text(message.isNotEmpty
            ? message
            : 'It looks like the install is done but the installer '
                "hasn't exited. Finish now?"),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep waiting'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Finish now'),
          ),
        ],
      ),
    );
    return answer ?? false;
  }

  Future<String> _onSelectExe(List<String> exes) async {
    if (!mounted) return exes.first;
    final selected = await showDialog<String>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Select executable'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Multiple executables found. Choose which one to add '
                'as the Steam shortcut:',
              ),
              const SizedBox(height: 12),
              for (final exe in exes)
                ListTile(
                  title: Text(exe.split(RegExp(r'[/\\]')).last),
                  subtitle: Text(exe),
                  onTap: () => Navigator.of(context).pop(exe),
                ),
            ],
          ),
        ),
      ),
    );
    return selected ?? exes.first;
  }

  Future<void> _cancel() async {
    setState(() => _cancelled = true);
    await _operation?.cancel();
  }

  Widget _buildProgress() {
    final progress = _progress;
    final phase = progress != null
        ? (_phaseLabels[progress.phase] ?? progress.phase)
        : 'Starting...';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          phase,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 12),
        LinearProgressIndicator(value: progress?.fraction),
        const SizedBox(height: 8),
        if (progress != null)
          Text(
            [
              if (progress.fraction != null)
                '${(progress.fraction! * 100).toStringAsFixed(0)}%',
              if (progress.rate > 0) humanRate(progress.rate),
              if (progress.message.isNotEmpty) progress.message,
            ].join('  -  '),
          ),
        const SizedBox(height: 16),
        Expanded(
          child: Card(
            child: ListView.builder(
              reverse: true,
              padding: const EdgeInsets.all(8),
              itemCount: _log.length,
              itemBuilder: (context, index) => Text(
                _log[_log.length - 1 - index],
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
            ),
          ),
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          icon: const Icon(Icons.cancel),
          label: const Text('Cancel install'),
          onPressed: _cancelled ? null : _cancel,
        ),
      ],
    );
  }

  Widget _buildDone() {
    final result = _result!;
    final steamAdded = result['steam_added'] as bool? ?? false;
    final manualSteps =
        (result['manual_steps'] as List<dynamic>? ?? []).cast<String>();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Icon(Icons.check_circle, color: Colors.green, size: 32),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                'Installed ${result['name']}',
                style: const TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text('Executable: ${result['exe']}'),
        Text(
          steamAdded
              ? 'Added to Steam (appid ${result['appid']}) and the app launcher.'
              : 'Steam shortcut NOT added.',
        ),
        if (manualSteps.isNotEmpty) ...[
          const SizedBox(height: 16),
          const Text(
            'Manual steps',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 4),
          for (final step in manualSteps)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.arrow_right, size: 20),
                  Expanded(child: Text(step)),
                ],
              ),
            ),
        ],
        if (!steamAdded && result['reason'] == 'steam_running') ...[
          const SizedBox(height: 16),
          FilledButton.icon(
            icon: const Icon(Icons.refresh),
            label: const Text('Steam closed - add shortcut now'),
            onPressed: _retrySteamAdd,
          ),
        ],
        const Spacer(),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Done'),
        ),
      ],
    );
  }

  Future<void> _retrySteamAdd() async {
    try {
      final data = await runBridgeOp('steam_add', {'target': widget.title},
          onSelectExe: _onSelectExe);
      if (!mounted) return;
      setState(() {
        _result = {
          ...?_result,
          'steam_added': true,
          'appid': data['appid'],
          'reason': null,
          'manual_steps': [
            'Set the Proton version in Steam: Properties > Compatibility.',
          ],
        };
      });
    } on BridgeException catch (err) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(err.message)));
    }
  }

  Widget _buildError() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Icon(
              Icons.error,
              color: Theme.of(context).colorScheme.error,
              size: 32,
            ),
            const SizedBox(width: 12),
            const Expanded(
              child: Text(
                'Install failed',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(_error!),
        const Spacer(),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Back'),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final Widget body;
    if (_result != null) {
      body = _buildDone();
    } else if (_error != null) {
      body = _buildError();
    } else {
      body = _buildProgress();
    }
    return Scaffold(
      appBar: AppBar(title: Text('Installing ${widget.title}')),
      body: Padding(padding: const EdgeInsets.all(16), child: body),
    );
  }
}
