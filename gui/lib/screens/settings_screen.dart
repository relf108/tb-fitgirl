import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../bridge.dart';
import '../keyring.dart';
import '../models.dart';

/// Update, validate, or clear stored API keys (TorBox + optional SteamGridDB).
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.apiKey,
    required this.onKeyChanged,
  });

  final String apiKey;
  final void Function(String? key) onKeyChanged;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _controller = TextEditingController();
  final _sgdbController = TextEditingController();
  bool _busy = false;
  bool _sgdbBusy = false;
  AccountInfo? _account;
  String? _error;
  String? _sgdbKey;
  String? _sgdbStatus;

  static const _sgdbApiUrl =
      'https://www.steamgriddb.com/profile/preferences/api';

  @override
  void initState() {
    super.initState();
    _checkCurrent();
    _loadSgdb();
  }

  @override
  void dispose() {
    _controller.dispose();
    _sgdbController.dispose();
    super.dispose();
  }

  String get _maskedKey {
    final key = widget.apiKey;
    if (key.length <= 8) return '****';
    return '${key.substring(0, 4)}...${key.substring(key.length - 4)}';
  }

  String get _maskedSgdb {
    final key = _sgdbKey;
    if (key == null || key.isEmpty) return '(not set)';
    if (key.length <= 8) return '****';
    return '${key.substring(0, 4)}...${key.substring(key.length - 4)}';
  }

  Future<void> _checkCurrent() async {
    try {
      final data = await runBridgeOp('validate_key', {
        'api_key': widget.apiKey,
      });
      if (!mounted) return;
      setState(() => _account = AccountInfo.fromJson(data));
    } on BridgeException catch (err) {
      if (!mounted) return;
      setState(() => _error = 'Current key check failed: ${err.message}');
    }
  }

  Future<void> _loadSgdb() async {
    final key = await steamGridDbKeyStore.load();
    if (!mounted) return;
    setState(() => _sgdbKey = key);
  }

  Future<void> _updateKey() async {
    final key = _controller.text.trim();
    if (key.isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final data = await runBridgeOp('validate_key', {'api_key': key});
      final stored = await torboxKeyStore.save(key);
      if (!mounted) return;
      setState(() => _account = AccountInfo.fromJson(data));
      widget.onKeyChanged(key);
      _controller.clear();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            stored
                ? 'API key updated.'
                : 'Key valid, but keyring write failed.',
          ),
        ),
      );
    } on BridgeException catch (err) {
      if (!mounted) return;
      setState(() => _error = err.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _clearKey() async {
    await torboxKeyStore.clear();
    if (!mounted) return;
    widget.onKeyChanged(null);
    Navigator.of(context).pop();
  }

  Future<void> _saveSgdb() async {
    final key = _sgdbController.text.trim();
    if (key.isEmpty) return;
    setState(() {
      _sgdbBusy = true;
      _sgdbStatus = null;
    });
    final stored = await steamGridDbKeyStore.save(key);
    if (!mounted) return;
    setState(() {
      _sgdbBusy = false;
      _sgdbKey = key;
      _sgdbStatus = stored
          ? 'SteamGridDB key saved.'
          : 'Could not persist SteamGridDB key.';
    });
    _sgdbController.clear();
  }

  Future<void> _clearSgdb() async {
    await steamGridDbKeyStore.clear();
    if (!mounted) return;
    setState(() {
      _sgdbKey = null;
      _sgdbStatus = 'SteamGridDB key cleared.';
    });
  }

  Future<void> _openSgdbLink() async {
    try {
      final result = await Process.run('xdg-open', [_sgdbApiUrl]);
      if (result.exitCode == 0) return;
    } on ProcessException {
      // fall through
    }
    await Clipboard.setData(const ClipboardData(text: _sgdbApiUrl));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Link copied to clipboard.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    final account = _account;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480),
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              const Text(
                'TorBox account',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              Text('API key: $_maskedKey'),
              if (account != null)
                Text(
                  '${account.email} - ${account.planName}, expires ${account.expiry}',
                ),
              const Divider(height: 32),
              TextField(
                controller: _controller,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'New API key',
                  border: OutlineInputBorder(),
                ),
                onSubmitted: (_) => _updateKey(),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: _busy ? null : _updateKey,
                child: const Text('Validate & update'),
              ),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: _busy ? null : _clearKey,
                child: const Text('Clear stored key'),
              ),
              if (_error != null) ...[
                const SizedBox(height: 16),
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              const Divider(height: 40),
              const Text(
                'SteamGridDB (optional)',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              const Text(
                'Improves search-result thumbnails with community icons. '
                'Leave blank to use Steam store images only.',
              ),
              const SizedBox(height: 8),
              Text('API key: $_maskedSgdb'),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _openSgdbLink,
                  icon: const Icon(Icons.open_in_new, size: 18),
                  label: const Text('Get an API key'),
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _sgdbController,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'SteamGridDB API key',
                  border: OutlineInputBorder(),
                ),
                onSubmitted: (_) => _saveSgdb(),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: _sgdbBusy ? null : _saveSgdb,
                child: const Text('Save SteamGridDB key'),
              ),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: _sgdbBusy || _sgdbKey == null ? null : _clearSgdb,
                child: const Text('Clear SteamGridDB key'),
              ),
              if (_sgdbStatus != null) ...[
                const SizedBox(height: 16),
                Text(_sgdbStatus!),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
