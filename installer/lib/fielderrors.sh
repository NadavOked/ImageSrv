# fielderrors.sh -- the install GUI's failure-code tables (#1503). POSIX sh.
# Sourced by gui-bridge.sh only (function defs, nothing runs): which field
# owns an engine failure code, and the Hebrew line shown under it.

# The engine's failure code -> the GUI field that owns it (errors.<field>=).
field_for_code() {
    case "$1" in
        invalid-disk|iso-target|missing-key|invalid-raid|raid-virtual) printf disk ;;
        invalid-role) printf role ;; invalid-primary-url|primary-unverified) printf primary_url ;;
        invalid-interface|invalid-mac|mac-mismatch|interface-check) printf interface ;;
        invalid-network-mode) printf mode ;; invalid-address) printf address ;;
        invalid-netmask) printf netmask ;; invalid-gateway) printf gateway ;; invalid-dns) printf dns ;;
        invalid-network) printf address ;; invalid-hostname) printf hostname ;;
        invalid-timezone|zones-list-missing) printf timezone ;;
        invalid-admin-user) printf admin_user ;; invalid-admin-password) printf password ;;
        invalid-root-password|root-password-missing) printf root_password ;;   # #1632
        *) printf disk ;;
    esac
}

# The engine's failure code -> the Hebrew line under the field (#1503). The
# engine's own English message stays in the log (engine_validate prints it).
he_for_code() {
    case "$1" in
        iso-target) printf '%s' 'לא ניתן להתקין על המדיה שממנה אתחלתם' ;;
        missing-key) printf '%s' 'חסר פרט בהגדרות ההתקנה' ;;
        invalid-disk) printf '%s' 'הדיסק שנבחר אינו תקין' ;;
        invalid-raid) printf '%s' 'בחירת ה-RAID אינה תקינה — מספר הכוננים או הכוננים שנבחרו אינם מתאימים לרמה' ;;
        raid-virtual) printf '%s' 'זהו שרת וירטואלי: RAID נעשה ברמת המארח (ESXi/Hyper-V), לא בתוך המכונה' ;;
        invalid-role) printf '%s' 'תפקיד השרת אינו תקין' ;;
        invalid-primary-url) printf '%s' 'כתובת השרת הראשי אינה תקינה (כתובת IP או שם, או https://HOST:8443)' ;;
        primary-unverified) printf '%s' 'השרת הראשי לא אומת — הריצו בדיקת חיבור עד שהיא ירוקה' ;;
        invalid-interface) printf '%s' 'כרטיס הרשת שנבחר אינו תקין' ;;
        invalid-mac) printf '%s' 'כתובת ה-MAC של כרטיס הרשת אינה תקינה' ;;
        mac-mismatch) printf '%s' 'כתובת ה-MAC אינה שייכת לכרטיס הרשת שנבחר' ;;
        interface-check) printf '%s' 'לא הצלחנו לקרוא את כתובת ה-MAC של כרטיס הרשת' ;;
        invalid-network-mode) printf '%s' 'מצב הרשת חייב להיות DHCP או כתובת קבועה' ;;
        invalid-network) printf '%s' 'ב-DHCP יש להשאיר את שדות הכתובת ריקים' ;;
        invalid-address) printf '%s' 'כתובת ה-IP אינה תקינה' ;;
        invalid-netmask) printf '%s' 'מסכת הרשת אינה תקינה' ;;
        invalid-gateway) printf '%s' 'כתובת השער אינה תקינה' ;;
        invalid-dns) printf '%s' 'רשימת שרתי ה-DNS אינה תקינה (כתובות מופרדות בפסיק)' ;;
        invalid-hostname) printf '%s' 'שם השרת אינו תקין — שם מחשב באנגלית: אותיות לטיניות, ספרות ומקף, עד 15 תווים, בלי מקף בתחילה או בסוף' ;;
        invalid-timezone) printf '%s' 'אזור הזמן אינו מוכר — שם אזור של tzdata באנגלית, למשל Asia/Jerusalem' ;;
        zones-list-missing) printf '%s' 'רשימת אזורי הזמן חסרה במדיית ההתקנה — לא ניתן לבדוק את אזור הזמן' ;;
        invalid-admin-user) printf '%s' 'שם המנהל אינו תקין — אותיות קטנות, ספרות ו-. _ - עד 32 תווים, בלי מקף בתחילה' ;;
        invalid-admin-password) printf '%s' 'הסיסמה לא יכולה להיות ריקה' ;;
        invalid-root-password|root-password-missing) printf '%s' 'סיסמת ה-root לא יכולה להיות ריקה' ;;
        *) printf '%s' 'ההגדרות לא עברו בדיקה' ;;
    esac
}
