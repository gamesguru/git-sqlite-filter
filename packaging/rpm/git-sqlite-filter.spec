Name:           git-sqlite-filter
Version:        0.1.2
Release:        1%{?dist}
Summary:        Git clean/smudge filter for SQLite databases

License:        MIT
URL:            https://github.com/shane/git-sqlite-filter
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
Requires:       git
Requires:       sqlite
Requires:       python3

%description
Git clean/smudge filter for SQLite databases with noise reduction.

%prep
%autosetup

%build
# No build step needed for pure scripts

%install
install -D -m 0755 src/git_sqlite_filter/clean.py %{buildroot}%{_bindir}/git-sqlite-clean
install -D -m 0755 src/git_sqlite_filter/smudge.py %{buildroot}%{_bindir}/git-sqlite-smudge
install -D -m 0644 man/git-sqlite-clean.1 %{buildroot}%{_mandir}/man1/git-sqlite-clean.1
install -D -m 0644 man/git-sqlite-smudge.1 %{buildroot}%{_mandir}/man1/git-sqlite-smudge.1
install -D -m 0644 man/git-sqlite.1 %{buildroot}%{_mandir}/man1/git-sqlite.1

%files
%license LICENSE
%doc README.rst
%{_bindir}/git-sqlite-clean
%{_bindir}/git-sqlite-smudge
%{_mandir}/man1/git-sqlite-clean.1*
%{_mandir}/man1/git-sqlite-smudge.1*
%{_mandir}/man1/git-sqlite.1*

%changelog
* Fri Jan 02 2026 Shane <shane@example.com> - 0.1.0-1
- Initial release
