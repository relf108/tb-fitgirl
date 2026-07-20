import 'package:flutter/material.dart';

import '../bridge.dart';
import '../keyring.dart';
import '../models.dart';

/// First-run onboarding: prompt for the TorBox API key, validate it against
/// the account endpoint, and store it in the OS keyring.
class SetupScreen extends StatefulWidget {
  const SetupScreen({super.key, required this.onSaved});

  final void Function(String key) onSaved;

  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends State<SetupScreen> {
  final _controller = TextEditingController();
  bool _busy = false;
  String? _error;
  AccountInfo? _account;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _validateAndSave() async {
    final key = _controller.text.trim();
    if (key.isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
      _account = null;
    });
    try {
      final data = await runBridgeOp('validate_key', {'api_key': key});
      final account = AccountInfo.fromJson(data);
      final stored = await torboxKeyStore.save(key);
      if (!mounted) return;
      setState(() => _account = account);
      if (!stored) {
        _showSnack('Could not save API key — please try again.');
      }
      widget.onSaved(key);
    } on BridgeException catch (err) {
      if (!mounted) return;
      setState(() => _error = err.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _showSnack(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('tb-fitgirl setup')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Enter your TorBox API key',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 8),
                const Text(
                  'Find it at torbox.app > Settings > API. '
                  'The key is stored in your OS keyring, never in a file.',
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _controller,
                  obscureText: true,
                  autofocus: true,
                  decoration: const InputDecoration(
                    labelText: 'API key',
                    border: OutlineInputBorder(),
                  ),
                  onSubmitted: (_) => _validateAndSave(),
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _busy ? null : _validateAndSave,
                  child: _busy
                      ? const SizedBox(
                          height: 18,
                          width: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('Validate & save'),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  Text(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ],
                if (_account != null) ...[
                  const SizedBox(height: 16),
                  Text(
                    'Signed in as ${_account!.email} '
                    '(${_account!.planName}, expires ${_account!.expiry})',
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
