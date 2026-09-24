"""French.

The wording of the sections a legacy HomeTube user already knows — « Publicité
et Sponsors », « Coupes », « Qualité Vidéo », « Intégrations Vidéo », « Gestion
des Cookies », « Options Avancées », « Dossier racine (/) » — is taken from
legacy's own `app/translations/fr.py` rather than translated afresh, so someone
migrating recognises the page instead of relearning it.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, str] = {
    # === the layer itself ===
    "ui.language": "Langue",
    # === signing in (content_sdk.signin) ===
    "signin.banner_title": "🔒 Vous n'êtes pas connecté·e",
    "signin.banner_body": (
        "{app_title} a besoin de savoir qui vous êtes avant de pouvoir faire "
        "quoi que ce soit. Votre travail, vos fichiers et votre historique "
        "vous appartiennent : rien ci-dessous ne s'exécutera tant que vous ne "
        "serez pas connecté·e. Il faut une adresse e-mail et un clic — aucun "
        "mot de passe."
    ),
    "signin.banner_action": "Se connecter",
    "signin.sidebar_action": "🔒 Se connecter",
    "signin.elsewhere_label": "Autres surfaces",
    "signin.other_surface_hint": (
        "Déjà connecté·e sur une autre surface ? Rechargez la page — une seule "
        "session les couvre toutes."
    ),
    "signin.signed_in_as": "Connecté·e en tant que **{who}**{badge}",
    "signin.operator_badge": " · opérateur",
    "signin.sign_out": "Se déconnecter",
    # === the source offer (content_sdk.legal) ===
    "legal.source_code": "Code source",
    # === quotas (content_sdk.quota) ===
    "quota.usage_title": "**Votre consommation** ({days} derniers jours glissants)",
    "quota.usage_minutes": "{used} / {allowed} min de média",
    "quota.usage_storage": "{used} / {allowed} stockés",
    "quota.usage_jobs": "{used} / {allowed} tâches",
    "quota.media_minutes": (
        "Cela vous ferait dépasser {allowed} minutes de média sur les 30 "
        "derniers jours. Vos plus anciennes minutes se libèrent d'elles-mêmes "
        "en sortant de la fenêtre — ou voyez les deux issues ci-dessous."
    ),
    "quota.storage_bytes": (
        "Vous occupez {used}, et le plafond ici est de {allowed}. Supprimez ce "
        "dont vous n'avez plus besoin, ou voyez ci-dessous."
    ),
    "quota.active_jobs_one": (
        "Une seule tâche à la fois sur cette instance. Attendez la fin de la "
        "tâche en cours — elle continue, rien n'est perdu."
    ),
    "quota.active_jobs_many": (
        "{count} tâches à la fois sur cette instance. Attendez la fin de la "
        "tâche en cours — elle continue, rien n'est perdu."
    ),
    "quota.ways_forward": "**Deux issues, et les deux nous vont.**",
    "quota.self_host": "Hébergez-le vous-même. Gratuit, sans compte, sans quota.",
    "quota.full_setup": "Installation complète : {url}",
    "quota.hosted_cta": (
        "Ou restez ici et laissez quelqu'un d'autre tenir le serveur — [{label}]({url})"
    ),
    "quota.retention_note": (
        "Quel que soit votre choix : ce que vous avez produit ici reste "
        "téléchargeable depuis votre bibliothèque pendant {days} jours."
    ),
    # === notifications (content_sdk.notifications) ===
    "notifications.dismiss": "Masquer",
    # === statuses and relative times (content_sdk.status) ===
    "status.ago_unknown": "—",
    "status.ago_seconds": "il y a {value} s",
    "status.ago_minutes": "il y a {value} min",
    "status.ago_hours": "il y a {value} h",
    "status.ago_days": "il y a {value} j",
    "status.job.created": "créée",
    "status.job.validating": "validation",
    "status.job.planning": "planification",
    "status.job.queued": "en file d'attente",
    "status.job.running": "en cours",
    "status.job.succeeded": "réussie",
    "status.job.partially_succeeded": "partiellement réussie",
    "status.job.failed": "échouée",
    "status.job.cancelled": "annulée",
    # === HomeTube ===
    # why an output is not on offer
    "ht.reason.unavailable": "indisponible pour cette source",
    "ht.reason.missing_material": "cette source n'a pas de {materials}",
    "ht.reason.implementation_unavailable": (
        "nécessite un composant serveur ({operations})"
    ),
    "ht.reason.material_fallback": "un élément nécessaire",
    "ht.reason.runner_fallback": "un exécuteur",
    "ht.reason.policy_restricted": "bloqué par la politique du serveur",
    "ht.reason.generic": "indisponible",
    # the engine, in the sidebar
    "ht.backend_unreachable": "⚠️ Moteur injoignable à {url} — {error}",
    "ht.backend_online": "🟢 moteur v{version}",
    "ht.backend_offline": "🔴 moteur hors ligne",
    "ht.api_docs": "API · /docs",
    "ht.recent_jobs": "Tâches récentes",
    # the URL, and what the analysis found
    "ht.url_label": "Vidéo ou playlist URL",
    "ht.url_placeholder": (
        "youtube.com/watch?v=…   ·   ou une playlist : …/playlist?list=…"
    ),
    "ht.url_help": (
        "Collez une vidéo seule ou une playlist entière — le formulaire "
        "s'adapte à ce qu'est l'URL."
    ),
    "ht.analyzing": "🔍 Analyse en cours…",
    "ht.analyze_refused": "⚠️ Analyse impossible pour cette URL — {message}",
    "ht.analyze_failed": "⚠️ L'analyse a échoué — {error}",
    "ht.playlist_untitled": "Playlist",
    "ht.playlist_count": "📚 {count} vidéos",
    "ht.playlist_entries": "Vidéos de cette playlist ({count})",
    "ht.playlist_delivery_note": (
        "Chaque vidéo est téléchargée et livrée dans le dossier que vous avez choisi."
    ),
    "ht.untitled": "Sans titre",
    "ht.tech_up_to": "🎞️ jusqu'à {height}p{codecs}",
    "ht.tech_auto_suffix": " (+auto)",
    # what was asked last time
    "ht.remembered_banner": (
        "🕘 **Vous avez demandé ceci le {when}** : {what} vers `{where}`{state}. "
        "Le formulaire ci-dessous reprend ces choix. Garder le même dossier "
        "est ce qui permet à Content de voir ce qui s'y trouve déjà au lieu "
        "de le retélécharger."
    ),
    "ht.remembered_anything": "un téléchargement",
    "ht.remembered_root_folder": "le dossier racine",
    "ht.remembered_last_run": " — dernière exécution : {status}",
    # naming and destination
    "ht.playlist_name_label": "Nom de la playlist",
    "ht.playlist_name_help": (
        "Préfixe de chaque fichier téléchargé — chaque vidéo garde son propre "
        "numéro et son titre (par ex. « MonNom-001-premiere-video »). Le "
        "serveur l'assainit."
    ),
    "ht.audio_name_label": "Nom de l'audio",
    "ht.video_name_label": "Nom de la vidéo",
    "ht.name_help": (
        "Le nom que le moteur a calculé pour cette source (ADR 0017) — "
        "modifiez-le ou laissez-le tel quel. Intact, rien n'est envoyé et le "
        "serveur nomme les fichiers lui-même, pour arriver exactement à ce nom."
    ),
    "ht.name_placeholder": "nommé par le serveur",
    "ht.folder_label": "Destination",
    "ht.folder_root": "📁 Dossier racine (/)",
    "ht.folder_new": "➕ Nouveau dossier…",
    "ht.folder_help": (
        "Où le fichier atterrit dans la bibliothèque de livraison du serveur."
    ),
    "ht.folder_new_label": "Chemin du nouveau dossier (relatif)",
    # what to produce
    "ht.content_label": "Contenu",
    "ht.content_collection_heading": (
        "**Contenu** &nbsp;·&nbsp; chaque vidéo est téléchargée en"
    ),
    "ht.content_heading": (
        "**Contenu** &nbsp;·&nbsp; ce que cette source peut produire"
    ),
    "ht.preset_video": "🎬 Vidéo",
    "ht.preset_audio": "🎵 Audio seul",
    "ht.preset_subtitles": "💬 Sous-titres seuls",
    "ht.preset_custom": "🧩 Personnalisé…",
    "ht.nothing_producible": (
        "Rien ne peut être produit depuis cette source sur cette installation."
    ),
    "ht.output.video": "Vidéo",
    "ht.output.audio": "Audio",
    "ht.output.subtitles": "Sous-titres",
    "ht.output.transcript": "Transcription",
    "ht.output.summary": "Résumé",
    "ht.output.thumbnail": "Miniature",
    "ht.output.metadata": "Métadonnées",
    "ht.help_derivable": "Dérivé de la source (transcription/résumé).",
    "ht.help_unknown": "Tenté — faisabilité indéterminée.",
    "ht.blocked_prefix": "Indisponible pour cette source — ",
    # languages
    "ht.language_original": "originale — la voix propre de chaque vidéo",
    "ht.server_language_preference": "🌐 Préférence de langue du serveur : {chain}",
    "ht.language_from_server": "🌐 D'après la préférence de langue du serveur.",
    "ht.audio_languages_label": "Langues audio",
    "ht.audio_languages_help": (
        "Pistes audio à inclure (VO d'abord, puis les préférences de langue de "
        "votre serveur). Plusieurs = multi-audio intégré à la vidéo."
    ),
    "ht.audio_languages_collection_help": (
        "Appliqué à chaque vidéo de la playlist. Les éléments ne sont pas "
        "sondés au préalable : c'est donc une préférence — une vidéo garde les "
        "pistes qu'elle a, et se rabat sinon sur son meilleur audio."
    ),
    "ht.original_voice": "🗣️ Voix originale : {language}",
    "ht.subtitles_label": "Sous-titres",
    "ht.subtitles_help": (
        "Langues des sous-titres (intégrés à la vidéo, ou livrés en fichiers "
        "pour le préréglage sous-titres seuls)."
    ),
    "ht.subtitles_collection_help": (
        "Intégrés à chaque vidéo de la playlist quand elle en a — une vidéo "
        "sans la langue demandée n'en garde simplement aucun."
    ),
    "ht.subtitles_auto": "🤖 Sous-titres auto-générés disponibles : {languages}",
    "ht.subtitles_none": "💬 Aucune piste de sous-titres détectée sur cette source.",
    # sponsors
    "ht.sponsors_section": "📊 Publicité et Sponsors",
    "ht.sponsorblock_label": "SponsorBlock",
    "ht.sponsorblock_help": (
        "Supprimer ou marquer les segments sponsorisés (données "
        "communautaires SponsorBlock)."
    ),
    "ht.cut_quality_label": "Qualité de découpe",
    "ht.cut_quality_keyframes": (
        "⚡ Découpe rapide — conserve la vidéo d'origine (recommandé)"
    ),
    "ht.cut_quality_precise": (
        "🐢 Découpe exacte — réencode tout (plusieurs minutes par vidéo)"
    ),
    "ht.cut_quality_help": (
        "La découpe rapide retire les segments par copie de flux le long des "
        "keyframes existantes : elle se termine à la vitesse du "
        "téléchargement, conserve les codecs demandés, et la fin de la vidéo "
        "reste propre. Une borne peut glisser jusqu'à la keyframe la plus "
        "proche (généralement moins d'une seconde). La découpe exacte demande "
        "à yt-dlp des bornes à la frame près (--force-keyframes-at-cuts), ce "
        "qui réencode tout le fichier avec les codecs par défaut de ffmpeg : "
        "sur un extrait 4K de 2 min, cela a mesuré 17 s de téléchargement "
        "contre 8 min de CPU, et transformé de l'AV1/Opus en un fichier "
        "H.264/Vorbis plus gros."
    ),
    # cutting
    "ht.cutting_section": "✂️ Coupes",
    "ht.cut_enable": "Ne garder qu'un extrait",
    "ht.cut_start": "Début (HH:MM:SS)",
    "ht.cut_end": "Fin (HH:MM:SS)",
    "ht.cut_mode_label": "Mode de découpe",
    "ht.cut_mode_help": (
        "keyframes : copie de flux rapide et sans perte — les bornes se calent "
        "sur les keyframes les plus proches. precise : bornes à la frame près "
        "via un réencodage du segment (plus lent)."
    ),
    # video quality
    "ht.quality_section": "🎥 Qualité Vidéo",
    "ht.max_resolution": "Résolution maximale",
    "ht.resolution_help": "Détectées sur cette source.",
    "ht.preferred_codec": "Codec préféré",
    "ht.container": "Conteneur",
    # embedding
    "ht.embedding_section": "📦 Intégrations Vidéo",
    "ht.embed_metadata": "Intégrer les métadonnées",
    "ht.embed_thumbnail": "Intégrer la miniature",
    "ht.embed_chapters": "Intégrer les chapitres",
    "ht.embed_subtitles": "Intégrer les sous-titres à la vidéo ({languages})",
    # audio, transcript, summary
    "ht.audio_section": "🎵 Audio",
    "ht.audio_format": "Format audio",
    "ht.audio_format_help": (
        "« source » conserve le flux natif ; les autres transcodent."
    ),
    "ht.transcript_section": "📝 Transcription",
    "ht.transcript_format": "Format de transcription",
    "ht.transcript_format_help": (
        "JSON est la forme canonique et porte les timings. `text` en est la "
        "dérivation lisible — le meilleur fichier à garder à côté d'une vidéo "
        "dans votre bibliothèque. Demander `text` en demandant aussi un résumé "
        "fait construire au moteur sa propre transcription timée, ce qui, sur "
        "une source sans sous-titres, revient à transcrire deux fois."
    ),
    "ht.summary_section": "🧠 Résumé",
    "ht.summary_length": "Longueur du résumé",
    # cookies
    "ht.cookies_section": "🍪 Gestion des Cookies{flag}",
    "ht.cookies_ready": " · ✅ prêt",
    "ht.cookies_missing": " · ⚠️ fichier de cookies manquant",
    "ht.auth_label": "Authentification",
    "ht.auth_help": (
        "Identifiants cookies côté serveur (CONTENT_CREDENTIALS). Nécessaires "
        "pour les vidéos privées ou soumises à une restriction d'âge."
    ),
    "ht.cookies_declared_missing": (
        "⚠️ `{credential}` est déclaré mais son fichier n'est pas encore là — "
        "déposez votre export de cookies dans `{path}` (côté hôte : le dossier "
        "`./config`), puis lancez `make docker-update`. Les cookies débloquent "
        "les vidéos soumises à une restriction d'âge et rendent les "
        "téléchargements YouTube plus fiables — voir config/README.md."
    ),
    "ht.cookies_will_be_used": (
        "✅ Sera utilisé pour ce téléchargement : `{path}` · mis à jour {when}"
    ),
    "ht.cookies_none": (
        "Aucun identifiant configuré sur le serveur — pour ajouter des cookies "
        "YouTube, voir config/README.md."
    ),
    # advanced
    "ht.advanced_section": "⚙️ Options Avancées",
    "ht.extra_args": "Arguments yt-dlp supplémentaires",
    "ht.extra_args_help": (
        "Utilisateurs avertis uniquement — transmis à yt-dlp, par ex. "
        "--limit-rate 2M --proxy http://host:8080. Seuls les drapeaux réseau, "
        "géo, cadence et user-agent sont acceptés ; le serveur rejette tout le "
        "reste."
    ),
    "ht.extra_args_unparseable": (
        "Impossible d'interpréter les arguments supplémentaires (vérifiez vos "
        "guillemets)."
    ),
    # submitting
    "ht.download_button": "🎬  Télécharger",
    "ht.download_playlist_button": "🎬  Télécharger la playlist ({count} vidéos)",
    "ht.submit_refused": "Demande refusée : {message}",
    "ht.submit_failed": "Échec de l'envoi : {error}",
    "ht.request_preview": "🧾 GenerationRequest (ce qui sera envoyé)",
    # following the job
    "ht.job_not_found": "Tâche introuvable : {error}",
    "ht.steps_progress": "{done}/{total} étapes",
    "ht.step_downloading": "téléchargement · {percent} %",
    "ht.artifacts": "**Artefacts**",
    "ht.in_your_library": " · dans votre bibliothèque : {path}",
    "ht.download_artifact": "⬇︎ télécharger",
    "ht.no_artifacts": "aucun artefact",
    "ht.cancel": "Annuler",
    "ht.retry": "Relancer",
    "ht.events": "Événements",
    "ht.events_skipped": (
        "    … {count} événements step.progress (affichés en direct ci-dessus)"
    ),
    "ht.logs_section": "Logs (sortie yt-dlp / ffmpeg, par étape)",
    "ht.no_logs": "aucun log pour l'instant",
    # === Content Studio ===
    # Les *jetons* du contrat — types de source et de sortie, formats, codecs,
    # conteneurs, statuts de capacité, `optimize_for` — restent tels que le
    # moteur les écrit, dans toutes les langues. Seules les phrases autour
    # d'eux sont traduites ici.
    "studio.tagline": "toutes les sources, toutes les sorties — le contrat complet",
    # pourquoi une sortie n'est pas proposée
    "studio.reason.unavailable": "indisponible pour cette source",
    "studio.reason.missing_material": "cette source n'a pas de {materials}",
    "studio.reason.material_fallback": "élément nécessaire",
    "studio.reason.implementation_unavailable": "nécessite un exécuteur ({operations})",
    "studio.reason.policy_restricted": "bloqué par la politique du serveur",
    "studio.reason.generic": "indisponible",
    # le moteur, dans la barre latérale
    "studio.backend_unreachable": "⚠️ Moteur injoignable à {url} — {error}",
    "studio.backend_online": "🟢 moteur v{version}",
    "studio.backend_offline": "🔴 moteur hors ligne",
    "studio.api_docs": "API · /docs",
    "studio.recent_jobs": "Tâches récentes",
    # les sources
    "studio.sources_section": "1 · Sources",
    "studio.how_many_sources": "Combien de sources ?",
    "studio.source_type": "Type",
    "studio.url_label": "URL",
    "studio.auth_label": "Authentification",
    "studio.file_location_label": "Où se trouve-t-il ?",
    "studio.file_on_device": "Depuis cet appareil",
    "studio.file_on_server": "Sur le serveur",
    "studio.path_label": "Chemin (sous une racine d'entrée autorisée)",
    "studio.choose_files": (
        "Choisir un ou plusieurs fichiers — {megabytes} Mo maximum chacun"
    ),
    "studio.text_content": "Contenu texte",
    "studio.uploading": "Envoi de {filename} au moteur…",
    "studio.upload_failed": "Échec de l'envoi : {error}",
    # leur analyse
    "studio.analyze_button": "🔍 Analyser les sources",
    "studio.analyzing": "Analyse en cours…",
    "studio.analyze_refused": "Analyse refusée : {message}",
    "studio.analyze_failed": "L'analyse a échoué : {error}",
    "studio.untitled": "(sans titre)",
    "studio.tech_up_to": "🎞️ jusqu'à {height}p{codecs}",
    # les sorties
    "studio.outputs_section": "2 · Sorties",
    "studio.outputs_hint": (
        "Activez les sorties voulues ; chacune est produite à partir d'une source."
    ),
    "studio.output_blocked": "⛔️ aucune source ne peut produire ceci — {reasons}",
    "studio.from_source": "depuis la source",
    "studio.status_on_source": "↳ {status} sur {source}",
    # leurs options
    "studio.opt.max_height": "hauteur max",
    "studio.opt.codec": "codec",
    "studio.opt.container": "conteneur",
    "studio.opt.sponsorblock": "sponsorblock",
    "studio.opt.format": "format",
    "studio.opt.languages": "langues (séparées par des virgules)",
    "studio.opt.language": "langue",
    "studio.opt.length": "longueur",
    "studio.opt.target_language": "langue cible",
    "studio.opt.source_language": "langue source",
    # préférences et contraintes
    "studio.preferences_section": "Préférences et contraintes",
    "studio.allow_cloud": "autoriser les fournisseurs cloud",
    "studio.reuse_existing": "reuse_existing (réutiliser l'existant)",
    # le lancement
    "studio.launch_section": "3 · Lancement",
    "studio.request_preview": "Aperçu de la GenerationRequest",
    "studio.submit_button": "🚀 Lancer la tâche",
    "studio.submit_refused": "Demande refusée : {message}",
    "studio.submit_failed": "Échec de l'envoi : {error}",
    # le suivi de la tâche
    "studio.job_not_found": "Tâche introuvable : {error}",
    "studio.steps_progress": "{done}/{total} étapes",
    "studio.artifacts": "**Artefacts**",
    "studio.download_artifact": "⬇︎ télécharger",
    "studio.no_artifacts": "aucun artefact",
    "studio.cancel": "Annuler",
    "studio.retry": "Relancer",
}
