Name:           neomixer
Version:        %{neomixer_version}
# No %%{?dist}: one package that installs on every current Fedora
Release:        1
Summary:        Virtual audio mixer for PipeWire, with an EQ on every channel and remembered program routing
License:        MIT
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  python3-babel

Requires:       gtk4
Requires:       pipewire
Requires:       pipewire-pulseaudio
Requires:       pipewire-utils
Requires:       pulseaudio-libs
Requires:       python3
Requires:       python3-cairo
Requires:       python3-gobject
Requires:       python3-pulsectl
Requires:       python3-pulsectl-asyncio
Requires:       python3-pydantic
Requires:       wireplumber

%description
A virtual audio mixer for PipeWire with Voicemeeter's workflow: channel strips, A/B routing, a
parametric EQ on every channel, and programs that stay where you put them. A fork of Pulsemeeter.

%prep
%autosetup

%install
packaging/install-tree.sh %{buildroot}

%files
%license %{_datadir}/licenses/%{name}/LICENSE
%{_bindir}/%{name}
%{_prefix}/lib/%{name}/
%{_datadir}/applications/org.neomixer.NeoMixer.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.neomixer.NeoMixer.svg
%{_datadir}/locale/*/LC_MESSAGES/%{name}.mo

%changelog
* Sun Nov 01 2026 NeoMixer contributors - %{version}-%{release}
- Package for Fedora
