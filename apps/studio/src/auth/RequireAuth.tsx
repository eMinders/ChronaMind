import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./context";

/** Routes inside it need a signed-in user; anyone else goes to /login and comes back after. */
export function RequireAuth() {
  const { auth } = useAuth();
  const location = useLocation();
  if (!auth) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
