import { useState, type FormEvent } from "react";
import { useWorkspace } from "../state/Workspace";
import { Brand, Icon, ThemeSwitch } from "../components/Icon";
import { ErrorBox, Modal, Notice } from "../components/ui";
export function LoginPage() {
  const w = useWorkspace();
  const [error, setError] = useState(""),
    [pending, setPending] = useState(false),
    [help, setHelp] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError("");
    try {
      await w.login(
        String(form.get("username")),
        String(form.get("password")),
        form.get("remember") === "on",
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="login-shell">
      <section className="login-brand">
        <Brand />
        <div className="login-hero">
          <p className="eyebrow">ЗАПАСЫ → ВОЗМОЖНОСТИ</p>
          <h1>
            Нужные ресурсы
            <br />
            уже могут быть
            <br />
            на вашем складе.
          </h1>
        </div>
        <p className="login-copy">
          Проверяйте остатки до закупки.
          <br />
          Принимайте решения на основе характеристик,
          <br />
          источников и экспертизы.
        </p>
        <div className="login-steps">
          {[
            [
              "upload",
              "Соберите данные",
              "Остатки склада и потребность в одном месте",
            ],
            [
              "search",
              "Найдите подходящий запас",
              "Сопоставление по техническим характеристикам",
            ],
            [
              "shield",
              "Подтвердите решение",
              "Проверка эксперта и прозрачная история",
            ],
          ].map(([icon, title, detail]) => (
            <div className="login-step" key={title}>
              <span className="step-icon">
                <Icon name={`${icon}-accent`} />
              </span>
              <div>
                {title}
                <small>{detail}</small>
              </div>
            </div>
          ))}
        </div>
        <footer>StockExpert · платформа предзакупочной проверки</footer>
      </section>
      <section className="login-main">
        <ThemeSwitch />
        <form className="login-form" onSubmit={submit}>
          <div>
            <h2>Добро пожаловать</h2>
            <p className="muted" style={{ marginTop: 8 }}>
              Войдите в рабочее пространство компании.
            </p>
          </div>
          <label>
            Логин
            <input
              name="username"
              autoComplete="username"
              placeholder="name@company.ru"
              required
            />
          </label>
          <label>
            Пароль
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              placeholder="Введите пароль"
              required
            />
          </label>
          <div className="row between">
            <label className="checkbox-label">
              <input name="remember" type="checkbox" />
              Запомнить меня
            </label>
            <button
              type="button"
              className="link-button"
              onClick={() => setHelp(true)}
            >
              Не получается войти?
            </button>
          </div>
          {(error || w.error) && <ErrorBox message={error || w.error} />}
          <button className="btn-primary full-width" disabled={pending}>
            {pending ? "Входим…" : "Войти в рабочее пространство"}
          </button>
          <Notice title="Защищённый контур">
            Доступ по учётной записи вашей организации.
          </Notice>
        </form>
        <footer>Нужен доступ? Обратитесь к администратору.</footer>
      </section>
      {help && (
        <Modal title="Помощь со входом" onClose={() => setHelp(false)}>
          <p>
            Учётную запись создаёт администратор вашей организации. Проверьте
            логин, раскладку клавиатуры и пароль. Если доступ утрачен, попросите
            администратора восстановить его.
          </p>
          <p className="muted small" style={{ marginTop: 16 }}>
            «Запомнить меня» сохраняет вход в этом браузере до истечения срока
            действия токена. На общем компьютере оставьте флажок выключенным.
          </p>
          <div className="modal-actions">
            <button className="btn-primary" onClick={() => setHelp(false)}>
              Понятно
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
