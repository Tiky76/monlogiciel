# APK de scannage QR

Le dossier `android-scanner/` contient une base Android WebView pour creer une APK
de scannage. L'APK ouvre la page `/scanner` de la billetterie, qui utilise la
camera du telephone pour lire le QR Code, interroger l'API et valider
l'embarquement.

## Principe

1. L'application Flask tourne sur un serveur accessible par le telephone.
2. L'agent se connecte avec son compte.
3. La page `Scanner QR` lit le QR Code du billet.
4. L'API `/api/verify/<token>` controle le billet.
5. Le bouton de validation appelle `/api/verify/<token>/use`.

## Configurer l'adresse du serveur

Dans `android-scanner/app/src/main/res/values/strings.xml`, remplacer
`http://192.168.1.101:5000/scanner` par l'adresse reelle du serveur.

Exemples:

```text
http://192.168.1.101:5000/scanner
https://billetterie.example.com/scanner
```

Pour un vrai deploiement, utiliser HTTPS afin que la camera fonctionne de facon
fiable sur Android.

## Generer l'APK

La machine actuelle n'a pas Android SDK ni Gradle installes, donc l'APK ne peut
pas etre compile ici directement. Pour le generer:

1. ouvrir le dossier `android-scanner/` dans Android Studio;
2. laisser Android Studio installer Gradle et le SDK Android;
3. verifier l'adresse du serveur dans `strings.xml`;
4. lancer `Build > Build Bundle(s) / APK(s) > Build APK(s)`.

L'APK genere se trouvera dans:

```text
android-scanner/app/build/outputs/apk/debug/
```

Si Gradle est disponible dans le terminal, vous pouvez aussi lancer:

```powershell
.\android-scanner\build-apk.ps1
```

## Generer l'APK avec GitHub

Le projet contient aussi un workflow GitHub Actions:

```text
.github/workflows/build-android-apk.yml
```

Apres avoir envoye le projet sur GitHub:

1. ouvrir le depot GitHub;
2. aller dans l'onglet `Actions`;
3. choisir `Generer APK Scanner QR`;
4. cliquer sur `Run workflow`;
5. renseigner `server_url` avec l'adresse de la page scanner;
6. attendre la fin de la compilation;
7. telecharger l'artifact `scanner-billetterie-apk`.

Exemple de `server_url`:

```text
http://192.168.1.101:5000/scanner
```

Pour un usage reel avec plusieurs telephones, preferer une adresse HTTPS publique.

## Si Android affiche "Application non installee"

1. Supprimer toute ancienne application appelee `Scanner Billetterie` ou
   `Scanner TicketBus`.
2. Installer uniquement le fichier `.apk`, pas le fichier `.zip`.
3. Autoriser l'installation depuis WhatsApp, Chrome ou le gestionnaire de
   fichiers.
4. Si l'erreur continue, generer une nouvelle version APK depuis GitHub Actions.

La version 1.1 utilise l'identifiant Android
`com.ticketbus.billetterie.scanner` pour eviter les conflits avec les anciens
essais signes differemment.
