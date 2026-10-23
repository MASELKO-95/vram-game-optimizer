# VRAM Game Optimizer

Dokumentacja po polsku. Bieżące wydanie NVIDIA i pliki do pobrania: [strona projektu](../README.md#download). Wydanie AMD jest planowane, a osobne wydanie Intel powstanie, jeśli będzie wymagało dodatkowej obsługi.

A lightweight tool that helps reclaim VRAM and RAM used by your Linux desktop and background apps. Pick a Steam game, choose an optimization profile, and automatically restore your desktop when the game closes.

Lekka aplikacja terminalowa po polsku i wrapper uruchamiania gier. Na czas gry zatrzymuje wybrane elementy tła, mierzy pamięć i po zakończeniu przywraca wcześniejszy stan. Obsługuje natywny Steam na Linuksie, rozpoznaje środowisko graficzne i dobiera dostępne optymalizacje. Python 3.10+, bez zależności z pip. Menu korzysta z curses, a opcjonalne okno z Tk, bez silnika przeglądarki.

## Uruchomienie

**Wymagany jest włączony, zalogowany natywny Steam.** W tym katalogu uruchom w terminalu:

```bash
./game-optimizer
```

Program pokaże zainstalowane gry ze wszystkich dostępnych bibliotek Steam, również na dodatkowych dyskach. Wybierz grę **strzałkami ↑/↓ i Enterem**, następnie wybierz profil i naciśnij Enter, aby uruchomić. Wpisywanie fragmentu nazwy lub AppID filtruje listę. Backspace usuwa filtr, PgUp/PgDn przewijają, a Esc wraca lub zamyka menu.

Środowisko `venv` nie jest wymagane. Menu korzysta tylko z locale znaków (`LC_CTYPE`), więc nieprawidłowe kategorie formatowania KDE nie blokują startu. Jeśli ustawienia znaków są niepoprawne, program wybiera dostępne locale UTF-8 we własnym procesie, bez zmiany konfiguracji systemu.

Przy liście gier: **F2** wybiera aplikacje do zamknięcia, **F5** odświeża biblioteki, **F6** pokazuje diagnostykę, **F8** przywraca pulpit. Jeśli Steam jest wyłączony, program wyświetla wymaganie uruchomienia klienta i opcję ponownego sprawdzenia; nie zmienia wtedy pulpitu.

**Nie trzeba edytować opcji uruchamiania Steam.** Program przygotowuje pulpit, wysyła żądanie do działającego klienta i obserwuje procesy wybranej gry po jej AppID. Koniec procesu `steam -applaunch` nie kończy optymalizacji. Po zamknięciu gry przywraca pulpit i wraca do menu. Steam pozostaje aktywny. Ctrl+C podczas nadzorowania przywraca pulpit i pozostawia już uruchomioną grę; timeout startu domyślnie wynosi 180 sekund. Gra już działająca jest odrzucana przed zmianami.

Lista i uruchamianie bez menu:

```bash
./game-optimizer games
./game-optimizer steam 70 --profile vram
./game-optimizer steam 70 --profile vram-ram --launch-timeout 240
```

`70` jest przykładowym AppID; wybierz identyfikator swojej zainstalowanej gry z listy. Kod zakończenia sesji Steam opisuje nadzór, ponieważ klient nie udostępnia tu kodu wyjścia właściwej gry.

Opcjonalne okno graficzne:

```bash
./game-optimizer gui
```

## Opcjonalny wrapper w opcjach Steam

Można nadal uruchamiać optymalizację bez otwierania menu, przez opcje konkretnej gry: **Steam → gra → Właściwości → Ogólne → Opcje uruchamiania**. Tekst generuje `./game-optimizer steam-options` lub zakładka **Steam** w opcjonalnym oknie.

Wygeneruj opcje dla swojej kopii programu:

```bash
./game-optimizer steam-options --profile vram
```

`%command%` jest rozwijane przez Steam. Nie wpisuj go w zwykłym terminalu. Jeśli masz już własne opcje, zachowaj argumenty gry i przemyślaną kolejność wrapperów, np. `mangohud`. Nie dodawaj drugiej kopii `%command%`. Własny wrapper tego programu, już obecny w opcjach gry, dołącza do sesji rozpoczętej z menu, bez powtórnego wyłączania usług.

Gra spoza Steam:

```bash
./game-optimizer run --profile vram -- /ścieżka/do/gry --fullscreen
```

Plik lub ścieżka ze spacjami wymaga cudzysłowu. W polu komendy opcjonalnego okna argumenty są przekazywane bez powłoki, więc `$HOME`, `~`, `;` i przekierowania nie są tam interpretowane. Nie uruchamiaj całego programu przez `sudo`.

## Profile

| Profil | Obsługiwane efekty X11 | Panel i tapeta Plasmy (KDE) | Baloo (KDE) | MemoryLow gry |
| --- | --- | --- | --- | --- |
| `balanced` | Wyłączone | Działają | Bez zmian | 0 |
| `vram` | Wyłączone | Zatrzymane | Bez zmian | 0 |
| `vram-ram` | Wyłączone | Zatrzymane | Zatrzymane, jeśli aktywne | Do 2 GiB |

Każdy profil można zmienić flagami CLI lub checkboxami w opcjonalnym oknie. Domyślnie opcjonalny GameMode jest używany, jeśli dostępny. Wagi CPU wynoszą 1000 w profilu zrównoważonym i 10000 w pozostałych; waga I/O to 1000. Program próbuje ustawić nice −5, jeśli użytkownik ma takie uprawnienia. Nie używa harmonogramu czasu rzeczywistego.

Pod **F2** w terminalu lub w zakładce **Zwalnianie RAM** w oknie można dodatkowo zaznaczyć Discord, Spotify, Telegram, Firefox lub Chromium. Domyślnie żadna aplikacja nie jest zaznaczona. Zamknięcie odbywa się przez SIGTERM, bez wymuszania SIGKILL. **Zapisz pracę przed grą. Ponowne uruchomienie programu nie gwarantuje odtworzenia kart, rozmów, argumentów startowych ani niezapisanych danych.** Obsługiwane są natywne instalacje tych aplikacji, rozpoznawane po pliku wykonywalnym; aplikacje Flatpak nie są automatycznie zamykane.

## Środowiska graficzne

Wykrywanie korzysta z `XDG_CURRENT_DESKTOP` i informacji o sesji. Rozpoznaje również nazwy złożone, np. `ubuntu:GNOME` i `X-Cinnamon:GNOME`. Menu, diagnostyka i podgląd zmian pokazują wykryte środowisko. Poza KDE program nie odczytuje ani nie zatrzymuje usług Plasmy i Baloo.

| Środowisko | Optymalizacje pulpitu | Co pozostaje aktywne |
| --- | --- | --- |
| KDE Plasma / X11 | Wstrzymanie KWin, zatrzymanie Plasmy i opcjonalnie Baloo | Menedżer okien, serwer Xorg |
| KDE Plasma / Wayland | Zatrzymanie Plasmy i opcjonalnie Baloo | KWin i serwer Wayland |
| Xfce / X11 z Xfwm4 | Czasowe wyłączenie kompozycji przez xfconf | Menedżer okien, panel i tapeta |
| MATE / X11 z Marco | Czasowe wyłączenie kompozycji przez GSettings | Menedżer okien, panel i tapeta |
| GNOME, Cinnamon, Budgie, Pantheon | Wspólne opcje CPU/I/O, GameMode, RAM i wybrane aplikacje | Powłoka i kompozytor |
| LXQt, LXDE, i3, Openbox | Wspólne opcje CPU/I/O, GameMode, RAM i wybrane aplikacje | Panel, tapeta i kompozytor |
| Sway, Hyprland i pozostałe sesje Wayland | Wspólne opcje CPU/I/O, GameMode, RAM i wybrane aplikacje | Serwer obrazu i kompozytor |
| Nieznany pulpit | Wspólne opcje bez zmian w usługach i ustawieniach pulpitu | Pulpit |

Wspólne opcje działają w każdym z tych środowisk; zakresy systemd i ujemny nice nadal zależą od uprawnień. W sesjach bez obsługi zatrzymywania indeksowania profil `vram-ram` korzysta z ochrony RAM i zaznaczonych aplikacji. Panel i tapeta są zatrzymywane automatycznie wyłącznie w KDE.

Xfce wymaga `xfconf-query` i `xprop`; MATE wymaga `gsettings`, `dconf` i `xprop`. Program sprawdza faktyczny menedżer okien przez właściwości EWMH: zmiana Xfwm4 lub Marco jest pomijana, jeśli użytkownik uruchomił np. Compiz. Brak narzędzia, odczytu lub uprawnień powoduje pominięcie danej optymalizacji.

Sterowanie kompozycją Xfce i MATE wymaga jeszcze weryfikacji w rzeczywistych sesjach tych pulpitów.

Ustawienia Xfwm4 i Marco są zapisywane w dzienniku przed zmianą. W MATE odtwarzany jest również brak własnego ustawienia użytkownika, jeśli wcześniej działała wartość domyślna. Ponieważ są to preferencje zapisane na dysku, odzyskiwanie odtwarza je również po restarcie komputera, przed archiwizacją starego dziennika. Nie uruchamia wtedy usług starej sesji. Menu sprawdza dziennik przy starcie.

## Co faktycznie zwalnia VRAM

Zamknięcie Plasmy usuwa m.in. jej panel, tapetę i widżety. Wstrzymanie kompozycji X11 usuwa zasoby efektów. Zamknięcie wybranej aplikacji pozwala sterownikowi odzyskać jej zasoby GPU. Program pokazuje zmianę wolnego VRAM po tych czynnościach, **przed uruchomieniem gry**, osobno dla każdej wykrytej karty. Pomiar obejmuje cały system i może się zmieniać przez inne aplikacje. VRAM zarezerwowany przez sterownik może powodować, że `używane + wolne` nie równa się całkowitej pojemności.

Nie można usunąć całego użycia VRAM poza grą w działającej sesji graficznej. Xorg, bufory obrazu, sterownik i część usług pozostają potrzebne. Na Waylandzie kompozytor pełni rolę serwera wyświetlania, więc program go zachowuje. W KDE można opcjonalnie zatrzymać samą powłokę Plasmy. W GNOME i Cinnamon powłoka pozostaje aktywna.

Nie zabija Xorga, KWin, dźwięku, sieci, portali, usług kontrolerów ani nie wyłącza monitora. Nie wykonuje resetu GPU, overclockingu ani globalnych zmian sysctl.

## Steam przy mniejszym użyciu GPU

Przycisk **Steam oszczędny** działa po ręcznym zamknięciu aktualnego klienta. Uruchamia:

```bash
steam -silent -cef-disable-gpu
```

To ukrywa główne okno przy starcie i wyłącza akcelerację GPU interfejsu CEF. Klient oraz procesy CEF nadal istnieją; nie jest to całkowicie bezgraficzny Steam. Flagi istniejącego klienta nie są zmieniane. Program nie zamyka klienta ani nie zabija `steamwebhelper` podczas gry, ponieważ Steam, DRM i Steam Input mogą być grze potrzebne. Można też ręcznie wyłączyć nakładkę w ustawieniach danej gry. Zmiana flag CEF może przenieść część pracy na CPU; wynik warto porównać na własnej grze.

**Nie opieramy trybu oszczędnego na starych flagach `-no-browser` / `-vgui`.** Obsługa klienta Steam zmienia się i nie dają one tutaj gwarancji działania bez CEF.

Nie opakowuj `steam -applaunch ID` poleceniem `run`. Proces wysyłający żądanie do Steam może zakończyć się, zanim gra ruszy. Użyj menu lub `steam APPID`: te tryby rozpoznają procesy po `SteamAppId`, `SteamGameId` lub `STEAM_COMPAT_APP_ID` i zachowują ich tożsamość także po zmianach środowiska. Alternatywnie wrapper może znajdować się w opcjach konkretnej gry, przed `%command%`.

Steam Flatpak jest wykrywany w bibliotekach, ale uruchomienie wrappera na hoście wymaga osobnej integracji z sandboxem; ta wersja jej nie konfiguruje. Obsługiwany wariant to natywny Steam.

## CPU, I/O i RAM

- Gra działa w osobnym zakresie systemd użytkownika z wysoką wagą CPU i I/O. Wagi oznaczają pierwszeństwo przy rywalizacji z grupami tego samego poziomu; nie rezerwują wszystkich rdzeni. Skuteczność zależy od dostępności i delegacji kontrolerów cgroup v2, układu grup oraz obsługi I/O przez urządzenie i scheduler.
- W trybie menu / `steam APPID` zakres tworzony jest dla wykrytych procesów gry przez D-Bus systemd, a nowe procesy są dołączane. Priorytet samego klienta Steam pozostaje bez zmian. Jeżeli uprawnienia uniemożliwiają dołączenie procesu, program informuje o tym i korzysta z pozostałych dostępnych ustawień. GameMode jest zgłaszany dla PID gry; istniejąca rejestracja GameMode nie jest usuwana przez program.
- Nice −5 jest stosowane tylko przy posiadaniu uprawnień. Brak uprawnień nie blokuje gry; komunikat trafia do dziennika. Program nie wyświetla żądania sudo. Można zmienić wartość od −10 do 0; ustawienie skrajnych priorytetów nie gwarantuje wyższych FPS.
- Jeżeli systemd nie potrafi uruchomić zakresu, program ponawia **raz**, bez niego, wyłącznie gdy proces gry jeszcze nie ruszył. Nie ponawia gry, która już wystartowała i zwróciła błąd.
- `MemoryLow` jest żądaniem ochrony używanej pamięci przed odzyskiwaniem. Program ogranicza je do 25% bieżącego `MemAvailable`, pozostawiając co najmniej 2 GiB. Wymaga odpowiedniej ochrony na poziomie grup nadrzędnych; sam zakres użytkownika nie daje gwarancji działania. To nie zwiększa RAM ani VRAM i nie jest rezerwacją pamięci na wyłączność.
- Dostępny RAM pochodzi z `MemAvailable`, obejmującego pamięć możliwą do odzyskania przez jądro. Opróżnianie cache dysku często pogarsza doczytywanie zasobów, więc program nie używa `drop_caches` i nie wyłącza swapu.

Opcjonalnie na Arch / Manjaro z włączonym multilib:

```bash
sudo pacman -S gamemode lib32-gamemode
```

Ustawienia GameMode zależą od jego konfiguracji, uprawnień i sprzętu. Samo wykrycie `gamemoderun` nie oznacza, że wszystkie optymalizacje daemona zadziałały. Program nie nadpisuje Twojego `gamemode.ini`.

## Przywracanie po grze i awarii

Przed każdą zmianą zapisuje dziennik w `~/.local/state/game-optimizer/session.json`. Uruchamia wyłącznie usługi, które wcześniej były aktywne i które sam zatrzymał; nie włącza wcześniej wyłączonej kompozycji. Błąd przywracania zachowuje dziennik do następnej próby. Nie można równocześnie uruchomić dwóch niezależnych sesji optymalizacji.

Osobny proces nadzorujący przeżywa zamknięcie okna i awarię procesu uruchamiającego, także SIGKILL. Obserwuje potomków przez grupę procesów, znacznik środowiska i, jeśli dostępny, własny cgroup. Gdy po zakończeniu gry nie ma już tych procesów przez 3 sekundy, przywraca pulpit. Powiązane procesy Wine, launchera lub crash reportera mogą opóźnić przywracanie. Gra przekazana do już działającego, niezależnego launchera, który nie zachowuje znacznika ani grupy, wymaga umieszczenia wrappera bliżej właściwego procesu gry.

Ręczne przywrócenie pulpitu, także podczas gry:

```bash
./game-optimizer restore
```

Przycisk **Przywróć pulpit teraz** robi to samo; gra może dalej działać. Przywrócone zostają też ustawione przez program wagi zakresu i odziedziczony nice, jeśli procesy nadal mają tę samą wartość. Gra może samodzielnie ustawić inne priorytety swoich wątków.

Odzyskiwanie po zniknięciu obu procesów programu, gdy gra już nie działa:

```bash
./game-optimizer recover
```

Nowa sesja gry i uruchomienie okna również sprawdzają osierocony dziennik. W trybie Steam proces przywracania potrafi przejąć również oczekiwanie na start gry po wysłaniu żądania do klienta. Dzienniki z poprzedniego uruchomienia systemu lub innej sesji pulpitu są archiwizowane. Program nie może zagwarantować przywrócenia, jeśli równocześnie zginą oba procesy nadzorujące i nie zostanie ponownie uruchomiony albo system straci zasilanie; przy logowaniu pulpit uruchamia zwykły mechanizm KDE.

## Diagnostyka i tryb podglądu

```bash
./game-optimizer diagnose
./game-optimizer diagnose --json
./game-optimizer run --profile vram-ram --dry-run
./game-optimizer steam-options --profile vram
```

Podgląd `--dry-run` niczego nie zatrzymuje i nie uruchamia gry. NVIDIA: pomiary przez `nvidia-smi`, również procesy graficzne. AMD: podstawowe pomiary VRAM z sysfs DRM, jeśli sterownik je udostępnia. Brak odczytu VRAM nie blokuje uruchomienia gry. iGPU ze współdzieloną pamięcią nie jest tutaj traktowane jak karta z dedykowanym VRAM.

Przykładowe modyfikacje profilu:

```bash
./game-optimizer run --profile vram --no-plasma --no-gamemode -- /ścieżka/do/gry
./game-optimizer run --profile vram-ram --close-app discord --close-app spotify -- /ścieżka/do/gry
./game-optimizer run --profile vram --cpu-weight 10000 --io-weight 10000 --nice -5 -- /ścieżka/do/gry
```

Ustawienia okna i preferencje terminala: `~/.config/game-optimizer/settings.json`. Dzienniki: `session.log` i `launcher.log`. Ostatni pomiar: `last-report.json` w katalogu stanu. Obsługiwane są `XDG_CONFIG_HOME` i `XDG_STATE_HOME`.

## Instalacja dla użytkownika

Bez instalacji można korzystać z programu w obecnym katalogu. Dla stałej lokalizacji i wpisu w menu KDE:

```bash
python3 tools/install.py
```

Instalator kopiuje program do `~/.local/share/game-optimizer`, tworzy `~/.local/bin/game-optimizer` oraz ikonę i wpis w menu. Nie pobiera pakietów. Jeśli brakuje Tk:

```bash
sudo pacman -S python tk
```

Opcjonalne sprawdzanie dziennika przy logowaniu:

```bash
python3 tools/install.py --autostart-recovery
```

Po instalacji skopiuj opcje Steam ponownie z zainstalowanej kopii, aby wskazywały stałą lokalizację. Nie przenoś ani nie usuwaj używanego katalogu programu w czasie aktywnej sesji gry.

## Licencja

Projekt jest udostępniany na licencji [BSD-3-Clause](../LICENSE).

## Źródła mechanizmów

- [GameMode: funkcje i integracja Steam](https://github.com/FeralInteractive/gamemode)
- [Konfiguracja GameMode i priorytetów](https://github.com/FeralInteractive/gamemode/blob/master/example/gamemode.ini)
- [systemd: CPUWeight, IOWeight i MemoryLow](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
- [systemd: StartTransientUnit i AttachProcessesToUnit](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html)
- [KDE: akcja Suspend Compositing w implementacji X11](https://github.com/KDE/kwin/blob/Plasma/6.3/src/compositor_x11.cpp)
- [Xfwm4: właściwości xfconf](https://github.com/xfce-mirror/xfwm4/blob/master/src/settings.c)
- [Marco: schemat ustawień kompozytora](https://github.com/mate-desktop/marco/blob/master/src/org.mate.marco.gschema.xml)
- [GNOME Mutter: rola kompozytora i serwera obrazu](https://mutter.gnome.org/)
- [Valve: użycie -cef-disable-gpu w kliencie Linux](https://github.com/ValveSoftware/steam-for-linux/issues/10514)
- [Jądro Linux: drop_caches i jego koszt](https://docs.kernel.org/admin-guide/sysctl/vm.html#drop-caches)
