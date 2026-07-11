/// Presentation-side views of the bridge's JSON payloads.
library;

class SearchResult {
  const SearchResult({
    required this.title,
    required this.url,
    required this.cached,
    required this.sizeHuman,
    required this.source,
  });

  final String title;
  final String url;
  final bool cached;
  final String sizeHuman;
  final String source;

  factory SearchResult.fromJson(Map<String, dynamic> json) => SearchResult(
        title: json['title'] as String? ?? '',
        url: json['url'] as String? ?? '',
        cached: json['cached'] as bool? ?? false,
        sizeHuman: json['size_human'] as String? ?? '',
        source: json['source'] as String? ?? '',
      );
}

class AccountInfo {
  const AccountInfo({
    required this.email,
    required this.planName,
    required this.expiry,
  });

  final String email;
  final String planName;
  final String expiry;

  factory AccountInfo.fromJson(Map<String, dynamic> json) => AccountInfo(
        email: json['email'] as String? ?? '',
        planName: json['plan_name'] as String? ?? '',
        expiry: json['expiry'] as String? ?? '',
      );
}

String humanRate(double bytesPerSec) {
  var value = bytesPerSec;
  for (final unit in ['B/s', 'KB/s', 'MB/s', 'GB/s']) {
    if (value < 1024 || unit == 'GB/s')
      return '${value.toStringAsFixed(1)} $unit';
    value /= 1024;
  }
  return '${value.toStringAsFixed(1)} GB/s';
}
