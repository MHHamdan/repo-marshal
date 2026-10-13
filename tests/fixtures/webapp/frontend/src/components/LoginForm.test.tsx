import { render, screen } from "@testing-library/react";
import { LoginForm } from "./LoginForm";

test("renders the email field", () => {
  render(<LoginForm onSubmit={() => undefined} />);
  expect(screen.getByLabelText("Email")).toBeTruthy();
});
