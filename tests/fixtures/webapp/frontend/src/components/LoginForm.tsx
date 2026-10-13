import "../styles/login.css";

export interface LoginFormProps {
  onSubmit: (email: string) => void;
}

export function LoginForm({ onSubmit }: LoginFormProps) {
  return (
    <form
      className="login-form"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        onSubmit(String(data.get("email") ?? ""));
      }}
    >
      <label htmlFor="email">Email</label>
      <input id="email" name="email" type="email" required />
      <button type="submit">Sign in</button>
    </form>
  );
}
