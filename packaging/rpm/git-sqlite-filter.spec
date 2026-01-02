Name:           git-sqlite-filter
Version:        0.1.0
Release:        1%{?dist}
Summary:        Git clean/smudge filter for SQLite databases

License:        MIT
URL:            https://github.com/shane/git-sqlite-filter
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  python3-build
Requires:       git
Requires:       sqlite

%description
Git clean/smudge filter for SQLite databases with noise reduction.

%prep
%autosetup

%build
%pyproject_wheel

%install
%pyproject_install
install -D -m 0644 man/git-sqlite-clean.1 %{buildroot}%{_mandir}/man1/git-sqlite-clean.1
install -D -m 0644 man/git-sqlite-smudge.1 %{buildroot}%{_mandir}/man1/git-sqlite-smudge.1
install -D -m 0644 man/git-sqlite.1 %{buildroot}%{_mandir}/man1/git-sqlite.1

%files
%license LICENSE
%doc README.rst
%{_bindir}/git-sqlite-clean
%{_bindir}/git-sqlite-smudge
%{python3_sitelib}/git_sqlite_filter/
%{python3_sitelib}/git_sqlite_filter-%{version}.dist-info/
%{_mandir}/man1/git-sqlite-clean.1*
%{_mandir}/man1/git-sqlite-smudge.1*
%{_mandir}/man1/git-sqlite.1*

%changelog
* Fri Jan 02 2026 Shane <shane@example.com> - 0.1.0-1
- Initial release
