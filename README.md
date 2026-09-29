# PwCatBot

## Локальна база даних

Робочі JSON-файли та іконки з папки `data` можна тимчасово приховати від Git:

```powershell
.\scripts\protect-local-db.ps1
```

Коли потрібно навмисно передати актуальну базу через Git, поверніть файли під контроль,
зробіть окремий коміт із БД, а після цього знову ввімкніть захист:

```powershell
.\scripts\track-local-db.ps1
# git add/commit/push для потрібних файлів data
.\scripts\protect-local-db.ps1
```
